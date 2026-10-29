// duck.js: DuckDB in the browser (DuckDB-WASM), for the SQL box of an endpoint whose data must
// stay in this tab. It builds the same database as the backend (code/webapp/backend/sql_box.py
// open_database, with the tables of sql_routes.py): the data table (from a CSV file, a Parquet
// file or the embedded points) and the metadata tables "source", "fields" and "summaries".
// Then it locks the database: no more files, no internet, no extensions, no setting changes.
//
// DuckDB-WASM is big (a few MB of WebAssembly, about 400 MB of memory while it runs), so the
// components load this file only when they need it, with import():
//
//   import('./browser/duck.js')
//     .then((duck) => duck.openDatabase({ store: rdf.store, prefixes: rdf.prefixes, source: rdf.source,
//                                          documentIri: endpoint.iri, dataFile: file, points: rdf.points,
//                                          endpointId: endpoint.id }))
//     .then((db) => {
//       db.tables      // [the data table: { name, kind, columns: [{ name, type, field }], error }]
//       db.skipped     // [{ name, error }]: the tables that could not be loaded, and why
//       db.error       // null, or why no query can run (e.g. tss:from is no date-time)
//       db.run('SELECT 1').then((result) => ...)   // { columns, rows, moreRows, seconds }
//       db.close()     // stops DuckDB (its Web Worker) and frees its memory: call it when the endpoint is left
//     })
// sqlBox.js gives the routes' answers from a db (sqlTables, runSql); dataReading.js reads a
// Parquet file through db.parquetCells. Two files help this one: arrowValues.js writes the
// values DuckDB answers with like Python does, and sqlCheck.js checks that a query is one SELECT.
//
// Where DuckDB's files come from: the WebAssembly and its worker are bundled by Vite (?url, the
// same server as the app); the two extensions it needs (icu for time zones, parquet) are served
// by the app from public/vendor/duckdb-extensions/ (npm run fetch-browser-assets downloads them).
// So DuckDB never fetches anything from another server.
//
// A note on the style: DuckDB-WASM answers every call with a Promise, so this file uses async
// functions, unlike the rest of the app. "async function f()" gives a Promise; inside it,
// "await x" waits for the Promise x, like x.then(...) would, before the next line runs. Callers
// still use .then(). Big whole numbers (BIGINT, times in microseconds) come from DuckDB as
// BigInt values: 5n is the BigInt 5, and BigInts only calculate with other BigInts.

import * as duckdb from '@duckdb/duckdb-wasm'
import mvpWasm from '@duckdb/duckdb-wasm/dist/duckdb-mvp.wasm?url'
import mvpWorker from '@duckdb/duckdb-wasm/dist/duckdb-browser-mvp.worker.js?url'
import ehWasm from '@duckdb/duckdb-wasm/dist/duckdb-eh.wasm?url'
import ehWorker from '@duckdb/duckdb-wasm/dist/duckdb-browser-eh.worker.js?url'
import {
  RDFS_LABEL,
  TSS_CSV,
  TSS_DESCRIBES_FIELD,
  TSS_FEATURE_OF_INTEREST,
  TSS_MUTABLE,
  TSS_PARQUET,
  TSS_PRODUCED_BY,
  TSS_SENSOR,
  SUMMARY_ATTRIBUTES,
  one,
  readSummary,
  sourceModifiedAt,
} from '../tss.js'
import { messageText, pyFloatRepr, pyRound } from './python.js'
import {
  LEXICAL_FORMS,
  TIME_DATATYPES,
  duckdbType,
  hasForm,
  literalTime,
  pythonBoolean,
  pythonIntegerText,
  pythonNumber,
  timeText,
  windowMoment,
} from './values.js'
import { booleanProperty, hasFormat, readSource } from './sourceReading.js'
import { firstCsvRecord } from './csvFile.js'
import { checkCsvSettings, csvHeader, pointRowsOf } from './dataReading.js'
import { arrowCells, converterFor, pythonLength } from './arrowValues.js'
import { checkStatements } from './sqlCheck.js'
import {
  MAX_BYTES,
  MAX_ROWS,
  MAX_SECONDS,
  METADATA_TABLES,
  TYPE_WORDS,
  sqlIdentifier,
  sqlText,
  tableName,
} from './sqlBox.js'

// The two builds of DuckDB-WASM: "eh" for browsers with WebAssembly exceptions (all current
// ones), "mvp" for the others. selectBundle picks one.
const BUNDLES = {
  mvp: { mainModule: mvpWasm, mainWorker: mvpWorker },
  eh: { mainModule: ehWasm, mainWorker: ehWorker },
}

// Where the app serves the extensions: an absolute address, because DuckDB fetches them from
// its worker. DuckDB adds /<version>/<platform>/<name>.duckdb_extension.wasm itself.
function extensionRepository() {
  return new URL(import.meta.env.BASE_URL + 'vendor/duckdb-extensions', self.location.href).href.replace(/\/$/, '')
}

// ---------------------------------------------------------------------------
// The tables (sql_routes.sql_table, sql_box.load_table, the metadata rows of sql_routes.run_sql)
// ---------------------------------------------------------------------------

// The name the data file gets inside DuckDB: the file name of its address, with odd characters
// as _ (DuckDB would read * ? [ ] in a file name as a pattern)
function duckFileName(location) {
  let name = location.split('?')[0].split('#')[0].split('/').pop()
  try {
    name = decodeURIComponent(name)
  } catch {
    // keep the name as it is
  }
  name = name.replace(/[^A-Za-z0-9._-]/g, '_')
  if (name.replace(/^[._]+|[._]+$/g, '') === '') name = 'data'
  return name
}

// A value of an embedded point for its column of the data table, as a SQL value (sql_routes.sql_value)
function pointSqlValue(literal, column) {
  if (literal === undefined || literal === null) return 'NULL'
  if (column.type === 'VARCHAR') return sqlText(literal.value)
  const wrong = () => new Error(`Column ${column.name} should be ${TYPE_WORDS[column.type]}, but the data has: ${literal.value}`)
  const datatypeIri = literal.datatype.value
  if (TIME_DATATYPES.includes(datatypeIri)) {
    const moment = literalTime(literal)
    if (moment === null) throw wrong()
    return `CAST(${sqlText(timeText(moment))} AS ${column.type})`
  }
  // rdflib reads the literal (toPython), and its text must have the XSD form of its datatype
  if (!hasForm(literal.value, datatypeIri)) throw wrong()
  const type = duckdbType(literal.datatype)
  if (type === 'BIGINT') {
    const text = pythonIntegerText(literal)
    if (text === null) throw wrong()
    return `CAST(${sqlText(text)} AS ${column.type})`
  }
  if (type === 'DOUBLE') {
    const number = pythonNumber(literal)
    if (number === null) throw wrong()
    return `CAST(${sqlText(pyFloatRepr(number))} AS ${column.type})`
  }
  if (type === 'BOOLEAN') return `CAST(${pythonBoolean(literal) ? 'true' : 'false'} AS ${column.type})`
  // A date or a time: DuckDB reads the text
  return `CAST(${sqlText(literal.value)} AS ${column.type})`
}

// The SQL table of the source: its name, where its data is, and its columns (one per data
// column, typed by the field's tss:datatype; Parquet keeps its own types). "error" says why it
// can't be loaded. (sql_routes.sql_table)
async function sqlTable(database, connection, data, info, reservedWords) {
  const byColumn = new Map() // column name -> its field
  for (const field of info.fields) {
    if (field.columnName !== null) byColumn.set(field.columnName, field)
  }

  const table = { name: 'data', kind: null, columns: [], error: null }
  let names = []                 // the columns, in the order of the data
  const nativeTypes = new Map()  // Parquet: column name -> its type in the file
  let points = []                // embedded: the points, read as RDF
  try {
    if (info.embedded === true) {
      // The table is named after the source, e.g. ex:zevenaarshaven-source -> zevenaarshaven_source
      table.name = tableName(info.node.value.split('#').pop().split('/').pop(), reservedWords)
      table.kind = 'points'
      for (const field of info.fields) {
        if (field.columnName !== null) names.push(field.columnName)
      }
      points = await pointRowsOf(data, info)
    } else {
      const location = info.dataFile
      if (location === null) throw new Error('The source has no tss:dataFile')
      table.name = tableName(location, reservedWords)
      if (!data.dataFile) throw new Error(`Data file not found: ${location}`)
      table.path = duckFileName(location)
      // DuckDB reads the File (or Blob) itself, in pieces, when a query needs it
      await database.registerFileHandle(table.path, data.dataFile, duckdb.DuckDBDataProtocol.BROWSER_FILEREADER, true)

      if (hasFormat(info, TSS_PARQUET)) {
        table.kind = 'parquet'
        const described = await connection.query(
          `DESCRIBE SELECT * FROM read_parquet(${sqlText(table.path)}, hive_partitioning = false)`)
        for (const row of described.toArray()) {
          names.push(row.column_name)                       // e.g. Timestamp
          nativeTypes.set(row.column_name, row.column_type) // e.g. TIMESTAMP WITH TIME ZONE
        }
      } else if (hasFormat(info, TSS_CSV)) {
        table.kind = 'csv'
        checkCsvSettings(info)
        table.delimiter = info.delimiter
        table.header = info.header
        const firstLine = await firstCsvRecord(data.dataFile, table.delimiter)
        // No header line: a column gets the column name of the field with its tss:columnIndex
        names = table.header ? firstLine : csvHeader(info, firstLine)
      } else {
        throw new Error(`The SQL box can't read data in tss:format ${messageText(info.format, data.prefixes)}`)
      }
    }
  } catch (error) {
    table.error = error.message
    return table
  }

  // CSV: the XSD form each column's cells must have, by column name (null: any text)
  table.forms = {}
  for (const name of names) {
    if (names.filter((other) => other === name).length > 1) {
      table.error = `Two columns are called '${name}'; a SQL table needs different column names.`
      return table
    }
    const field = byColumn.get(name)
    const datatype = field !== undefined ? field.datatype : null
    const sqlType = table.kind === 'parquet' ? nativeTypes.get(name) : duckdbType(datatype)
    // Each column says which field it holds (the field's IRI), or null when no field describes it
    table.columns.push({ name: name, type: sqlType, field: field !== undefined ? field.node.value : null })
    const form = datatype !== null && datatype.termType === 'NamedNode' ? LEXICAL_FORMS[datatype.value] : undefined
    table.forms[name] = form === undefined ? null : form
  }

  // Embedded points: one row per point, one SQL value per column (see pointSqlValue)
  if (table.kind === 'points') {
    table.rows = []
    try {
      for (const point of points) {
        const row = []
        for (const column of table.columns) {
          row.push(pointSqlValue(point[byColumn.get(column.name).node.value], column))
        }
        table.rows.push(row)
      }
    } catch (error) {
      table.error = `Could not load the embedded points: ${error.message}`
    }
  }
  return table
}

// How one text column of a CSV file becomes one typed SQL column (sql_box.column_expression):
// each cell's text must have the XSD form of the field's datatype, or the loading stops
function columnExpression(column, form, reservedWords) {
  const name = sqlIdentifier(column.name, reservedWords)
  const sqlType = column.type
  if (sqlType === 'VARCHAR') return `${name} AS ${name}`
  let fits = `TRY_CAST(${name} AS ${sqlType}) IS NOT NULL`
  if (form !== null) fits = `regexp_full_match(${name}, ${sqlText(form)}) AND ${fits}`
  const message = sqlText(`Column ${column.name} should be ${TYPE_WORDS[sqlType]}, but the data has: `)
  return `CASE WHEN ${name} IS NULL THEN NULL ` +
    `WHEN ${fits} THEN CAST(${name} AS ${sqlType}) ` +
    `ELSE error(${message} || ${name}) END AS ${name}`
}

// Copy the source's data into the database as one table (sql_box.load_table)
async function loadTable(connection, table, reservedWords) {
  const name = sqlIdentifier(table.name, reservedWords)

  if (table.kind === 'points') {
    const columnList = table.columns.map((column) => `${sqlIdentifier(column.name, reservedWords)} ${column.type}`)
    await connection.query(`CREATE TABLE ${name} (${columnList.join(', ')})`)
    try {
      if (table.rows.length > 0) {
        const values = table.rows.map((row) => '(' + row.join(', ') + ')')
        await connection.query(`INSERT INTO ${name} VALUES ${values.join(', ')}`)
      }
    } catch (error) {
      await connection.query(`DROP TABLE ${name}`) // no half-filled table
      throw new Error(`Could not load the embedded points: ${error.message}`, { cause: error })
    }
    return
  }

  let sql
  if (table.kind === 'parquet') {
    // A Parquet file has its own column types, so DuckDB keeps them
    sql = `CREATE TABLE ${name} AS SELECT * FROM read_parquet(${sqlText(table.path)}, hive_partitioning = false)`
  } else {
    // CSV cells are text: read every column as text, then turn each into its field's type
    const typed = table.columns.map((column) => columnExpression(column, table.forms[column.name], reservedWords))
    const asText = table.columns.map((column) => `${sqlText(column.name)}: 'VARCHAR'`)
    const options = [
      'auto_detect = false',
      `delim = ${sqlText(table.delimiter)}`,
      "quote = '\"'",
      "escape = '\"'",
      `header = ${table.header ? 'true' : 'false'}`,
      "nullstr = ''",
      `columns = {${asText.join(', ')}}`,
    ]
    sql = `CREATE TABLE ${name} AS\n` +
      `SELECT ${typed.join(', ')}\n` +
      `FROM read_csv(${sqlText(table.path)}, ${options.join(', ')})`
  }
  try {
    await connection.query(sql)
  } catch (error) {
    let message = error.message
    if (message.includes('Expected Number of Columns')) {
      message = `a row does not have the ${table.columns.length} columns of the header line. ` +
        `DuckDB says: ${message.split('\n')[0]}`
    }
    throw new Error(`Could not load table ${table.name}: ${message}`, { cause: error })
  }
}

// What a field is, in one word, for the role column of "fields" (sql_routes.column_role)
function columnRole(field) {
  if (field.isTime) return 'time'
  if (field.describes !== null) return 'describes'
  if (field.observedProperty !== null) return 'value'
  return null
}

// A term as SQL text: an IRI in full, a literal's text; NULL when there is none
function termSql(term) {
  return term === null || term === undefined ? 'NULL' : sqlText(term.value)
}

// A moment (or null) as a SQL TIMESTAMPTZ
function momentSql(moment) {
  return moment === null ? 'NULL' : `CAST(${sqlText(timeText(moment))} AS TIMESTAMPTZ)`
}

// One attribute of a stored summary for its column of "summaries" (sql_routes.summary_value).
// A value that does not fit its column becomes NULL: the SHACL tab reports it.
function summarySql(term, sqlType) {
  if (term === null) return 'NULL'
  if (sqlType === 'VARCHAR') return sqlText(term.value)
  if (term.termType !== 'Literal') return 'NULL'
  if (sqlType === 'TIMESTAMPTZ') return momentSql(literalTime(term))
  const number = pythonNumber(term)
  if (number === null) return 'NULL'
  if (sqlType === 'BIGINT') {
    if (!Number.isInteger(number)) return 'NULL'
    const text = duckdbType(term.datatype) === 'BIGINT' ? pythonIntegerText(term) : BigInt(number).toString()
    return `CAST(${sqlText(text)} AS BIGINT)`
  }
  return `CAST(${sqlText(pyFloatRepr(number))} AS DOUBLE)`
}

// The keys of SUMMARY_ATTRIBUTES (tss.js) -> the columns of "summaries" (source_reading.SUMMARY_ATTRIBUTES)
const SUMMARY_COLUMNS = {
  from: 'from_time', until: 'until_time', policy: 'missing_value_policy', count: 'value_count',
  missing: 'missing', firstTime: 'first_time', lastTime: 'last_time', minimum: 'observed_minimum',
  maximum: 'observed_maximum', mean: 'mean', median: 'median', generatedAt: 'generated_at',
}

// The rows of the metadata tables, as SQL values in the order of METADATA_TABLES (sql_routes.run_sql)
function metadataRows(data, info, loadedName, endpointId) {
  const store = data.store
  const node = info.node
  const label = one(store, node, RDFS_LABEL)
  const mutable = booleanProperty(store, node, TSS_MUTABLE)
  const loaded = loadedName === null ? 'NULL' : sqlText(loadedName)
  const source = [
    sqlText(node.value),
    label !== null ? sqlText(label.value) : sqlText(endpointId),
    loaded,
    termSql(info.format),
    termSql(info.timeField),
    momentSql(windowMoment(store, node, 'from', data.prefixes)),  // throws for a tss:from that is no date-time
    momentSql(windowMoment(store, node, 'until', data.prefixes)),
    termSql(one(store, node, TSS_PRODUCED_BY)),
    mutable === null ? 'NULL' : String(mutable),
    // when the source's metadata was last edited: its tss:modifiedAt as a time, or null (sql_routes.source_modified_at)
    momentSql(literalTime(sourceModifiedAt(store, node))),
  ]

  const fields = []
  const summaries = []
  const sqlTypes = new Map(METADATA_TABLES.summaries.columns)
  for (const field of info.fields) {
    const role = columnRole(field)
    fields.push([
      field.columnName === null ? 'NULL' : sqlText(field.columnName),
      loaded,
      sqlText(field.node.value),
      termSql(field.label),
      role === null ? 'NULL' : sqlText(role),
      termSql(field.datatype),
      termSql(field.unit),
      termSql(field.observedProperty),
      termSql(one(store, field.node, TSS_SENSOR)),
      termSql(one(store, field.node, TSS_FEATURE_OF_INTEREST)),
      termSql(one(store, field.node, TSS_DESCRIBES_FIELD)),
    ])

    const summary = readSummary(store, field.node)
    if (summary === null) continue
    const row = {
      field: sqlText(field.node.value),
      column_name: field.columnName === null ? 'NULL' : sqlText(field.columnName),
      sql_table: loaded,
    }
    for (const attribute of SUMMARY_ATTRIBUTES) {
      const column = SUMMARY_COLUMNS[attribute.key]
      row[column] = summarySql(summary.values[attribute.key], sqlTypes.get(column))
    }
    summaries.push(METADATA_TABLES.summaries.columns.map(([name]) => row[name]))
  }
  return { source: [source], fields: fields, summaries: summaries }
}

// ---------------------------------------------------------------------------
// openDatabase
// ---------------------------------------------------------------------------

// Start DuckDB in its Web Worker, with the extensions from this app's own server and the
// settings of the backend. Gives { database, connection }.
async function startDuckDB() {
  const bundle = await duckdb.selectBundle(BUNDLES)
  const worker = new Worker(bundle.mainWorker)
  const database = new duckdb.AsyncDuckDB(new duckdb.VoidLogger(), worker)
  try {
    await database.instantiate(bundle.mainModule, bundle.pthreadWorker)
    await database.open({})
    const connection = await database.connect()
    await connection.query(`SET custom_extension_repository = ${sqlText(extensionRepository())}`)
    try {
      await connection.query('LOAD icu')
      await connection.query('LOAD parquet')
      await connection.query('LOAD json') // also built into the backend's DuckDB; checkStatements (sqlCheck.js) needs it
    } catch (error) {
      throw new Error(`DuckDB could not load its extensions from ${extensionRepository()}: ${error.message.split('\n')[0]} ` +
        '(Are they downloaded? Run "npm run fetch-browser-assets" in code/webapp/frontend.)', { cause: error })
    }
    await connection.query("SET TimeZone = 'UTC'")
    await connection.query("SET temp_directory = ''")
    return { database: database, connection: connection }
  } catch (error) {
    await database.terminate()
    throw error
  }
}

// Every row of a Parquet file (a File or Blob), each value as text, without a database for an
// endpoint: DuckDB starts, reads the file, and stops again. style: see converterFor (arrowValues.js).
// Gives a Promise of { names, rows }. (observations.js uses it when it gets no getDatabase.)
export async function readParquetFile(blob, style) {
  const { database, connection } = await startDuckDB()
  try {
    await database.registerFileHandle('data.parquet', blob, duckdb.DuckDBDataProtocol.BROWSER_FILEREADER, true)
    const answer = await connection.query("SELECT * FROM read_parquet('data.parquet', hive_partitioning = false)")
    return arrowCells(answer, style)
  } finally {
    await database.terminate()
  }
}

// Start DuckDB for one endpoint and fill it like the backend does (see the top of this file).
//   store, prefixes, source, points: from the rdf prop (rdf.store, rdf.prefixes, rdf.source, rdf.points)
//   documentIri: the document's IRI (endpoint.iri); dataFile: the tss:dataFile contents (File/Blob) or null
//   endpointId: the label of the "source" row when the source has no rdfs:label (the backend uses the endpoint id)
// Gives a Promise of the db (see the top of this file). It is rejected only when DuckDB itself
// can't start; a problem with the data or the metadata is in db.skipped / db.error.
export async function openDatabase({ store, prefixes, source, documentIri, dataFile, points, endpointId }) {
  const data = { store: store, prefixes: prefixes || {}, source: source, points: points, dataFile: dataFile }
  const { database, connection } = await startDuckDB() // connection: for the user's queries
  try {
    const internal = await database.connect() // for reading the Parquet rows (dataReading.js)

    // DuckDB's reserved words: a name like "order" must be written in quotes
    const reservedWords = new Set()
    const keywords = await connection.query("SELECT keyword_name FROM duckdb_keywords() WHERE keyword_category = 'reserved'")
    for (const row of keywords.toArray()) reservedWords.add(row.keyword_name)

    // 1. The data table. A table that can't be loaded is skipped (with the reason), so the
    //    metadata tables can still be used.
    const info = readSource(store, source)
    const table = await sqlTable(database, connection, data, info, reservedWords)
    const skipped = []
    if (table.error === null) {
      try {
        await loadTable(connection, table, reservedWords)
      } catch (error) {
        skipped.push({ name: table.name, error: error.message })
      }
    } else {
      skipped.push({ name: table.name, error: table.error })
    }
    const loaded = table.error === null && skipped.length === 0
    // The SQL text of the points is not needed any more
    delete table.rows

    // 2. The TSS metadata as tables. When a row can't be made (a tss:from that is no
    //    date-time, ...) no query can run: db.error says why, like the backend's answer.
    let metadataError = null
    let rows = null
    try {
      rows = metadataRows(data, info, table.error === null ? table.name : null, endpointId || documentIri || '')
    } catch (error) {
      metadataError = error.message
    }
    for (const name of Object.keys(METADATA_TABLES)) {
      const columnList = METADATA_TABLES[name].columns.map(([column, sqlType]) => `${column} ${sqlType}`)
      await connection.query(`CREATE TABLE ${name} (${columnList.join(', ')})`)
      if (rows === null || metadataError !== null) continue
      for (const row of rows[name]) {
        try {
          await connection.query(`INSERT INTO ${name} VALUES (${row.join(', ')})`)
        } catch (error) {
          metadataError = `A row can't go into the ${name} table (is tss:from / tss:until a date-time?): ${error.message}`
        }
      }
    }

    // 3. Lock it. From here on the database can only work with what is already in it.
    await connection.query('SET autoinstall_known_extensions = false')
    await connection.query('SET autoload_known_extensions = false')
    await connection.query("SET memory_limit = '1GB'")
    // (SET threads = 4 of the backend is left out: this DuckDB runs in one thread)
    await connection.query('SET enable_external_access = false') // no files, no internet, no extensions
    await connection.query('SET lock_configuration = true')      // and nobody can switch that back on
    if (table.path) await database.dropFile(table.path)           // the rows are in the table now

    return makeDb(database, connection, internal, table, skipped, metadataError, reservedWords, loaded)
  } catch (error) {
    await database.terminate()
    throw error
  }
}

// The db object openDatabase gives
function makeDb(database, connection, internal, table, skipped, metadataError, reservedWords, loaded) {
  // One user query at a time: a new one waits for the one before
  let queue = Promise.resolve()

  // Run one read-only query (sql_box.run_query): exactly one SELECT, at most MAX_SECONDS seconds,
  // MAX_ROWS rows and MAX_BYTES of values. Gives a Promise of { columns, rows, moreRows, seconds }.
  async function runOne(sql) {
    await checkStatements(database, connection, sql)

    const started = performance.now()
    let stopped = false
    const timer = setTimeout(() => {
      stopped = true
      connection.cancelSent()
    }, MAX_SECONDS * 1000)
    const columns = []
    const rows = []
    let moreRows = false
    try {
      const reader = await connection.send(sql, true)
      await reader.open() // reads the schema: the names and types of the columns
      const converters = []
      for (const field of reader.schema.fields) {
        columns.push(field.name)
        converters.push(converterFor(field.type, 'json'))
      }
      // Read the rows batch by batch, and stop as soon as the answer gets too big.
      // "for await": a for...of loop whose next batch is a Promise; it waits for each batch.
      let size = 0
      for await (const batch of reader) {
        const batchColumns = []
        for (let j = 0; j < converters.length; j++) batchColumns.push(batch.getChildAt(j))
        for (let i = 0; i < batch.numRows; i++) {
          if (rows.length === MAX_ROWS) {
            moreRows = true // one row more than we send: there are more
            break
          }
          const values = []
          for (let j = 0; j < converters.length; j++) {
            const value = batchColumns[j].isValid(i) ? converters[j].convert(batchColumns[j], i) : null
            size += pythonLength(value, converters[j].float)
            values.push(value)
          }
          if (size > MAX_BYTES) {
            const tooBig = new Error(`The answer is too big to show (more than ${MAX_BYTES / 1000000} MB). ` +
              'Ask for fewer rows or columns, or shorter values, e.g. with LIMIT or left(text, 100).')
            tooBig.ours = true // our own message: the catch below passes it on as it is
            throw tooBig
          }
          rows.push(values)
        }
        if (moreRows) break
      }
    } catch (error) {
      if (error.ours) throw error // the answer was too big (see above), not a DuckDB error
      if (stopped) throw new Error(`The query was stopped after ${MAX_SECONDS} seconds.`, { cause: error })
      let message = error.message
      if (message.includes('STDDEV_SAMP is out of range')) {
        message += '\n(SUMMARIZE also computes a standard deviation, and that fails when a column holds ' +
          'NaN or infinity. Use SELECT with min(), max() and avg() instead.)'
      }
      throw new Error(message, { cause: error })
    } finally {
      clearTimeout(timer)
      // Stop what is left of the query (when we stopped reading early)
      await connection.cancelSent().catch(() => false)
    }
    return { columns: columns, rows: rows, moreRows: moreRows, seconds: pyRound((performance.now() - started) / 1000, 2) }
  }

  return {
    tables: [table],
    skipped: skipped,
    error: metadataError,
    reservedWords: reservedWords,

    run(sql) {
      const result = queue.then(() => runOne(sql))
      queue = result.catch(() => null)
      return result
    },

    // The rows of the Parquet data table, each value as text (style 'preview': data_reading.cell_text,
    // 'rdf': tss2rdf.cell_text), for dataReading.js and the RDF generation. limit: a number of rows, or null.
    // Gives a Promise of { names, rows }.
    parquetCells(style, limit) {
      if (table.kind !== 'parquet' || !loaded) {
        const reason = skipped.length > 0 ? skipped[0].error : (table.error || 'the source is not a Parquet file')
        return Promise.reject(new Error(reason))
      }
      const sql = `SELECT * FROM ${sqlIdentifier(table.name, reservedWords)}` + (limit === null ? '' : ` LIMIT ${Number(limit)}`)
      return internal.query(sql).then((answer) => arrowCells(answer, style))
    },

    // Stop DuckDB: its Web Worker ends, and its memory is freed
    close() {
      return database.terminate()
    },
  }
}
