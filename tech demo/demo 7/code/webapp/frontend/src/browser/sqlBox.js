// sqlBox.js: the SQL box of an endpoint whose data stays in the browser. The same answers as
// the backend's SQL routes (code/webapp/backend/sql_routes.py) and the same tables, names and
// example queries as backend/sql_box.py. DuckDB itself runs in duck.js; this file has the parts
// that need no database: the metadata tables, how names are written, and the example queries.
//
// Usage (db comes from openDatabase in duck.js, for the same endpoint):
//   import { sqlTables, runSql } from './browser/sqlBox.js'
//
//   sqlTables(db, rdf.store, rdf.source)   // = GET /api/endpoints/{id}/sql/tables (a plain object, no Promise)
//   // { tables: [{ name, error, columns: [{ name, type, field }] }], metadataTables, examples, maxRows, maxSeconds }
//
//   runSql(db, 'SELECT * FROM fields')     // = POST /api/endpoints/{id}/sql
//     .then((answer) => ...)               // { columns, rows, moreRows, seconds, skipped }
//     .catch((error) => setError(error.message))   // DuckDB's own message, like the backend's "detail"

import { one, TSS_SUMMARY } from '../tss.js'
import { NUMERIC_TYPES } from './values.js'
import { readSource } from './sourceReading.js'

export const MAX_ROWS = 1000          // rows in an answer
export const MAX_BYTES = 5000000      // about 5 MB of values in an answer
export const MAX_SECONDS = 30         // a query that takes longer is stopped

// How the error messages describe each type (sql_box.TYPE_WORDS)
export const TYPE_WORDS = {
  TIMESTAMPTZ: 'a date-time like 2021-03-03T23:45:00Z',
  DATE: 'a date like 2021-03-03',
  TIME: 'a time like 23:45:00',
  DOUBLE: 'a number',
  BIGINT: 'a whole number',
  BOOLEAN: 'true, false, 1 or 0',
}

// The three metadata tables, and their columns (sql_box.METADATA_TABLES). IRIs are written in full.
export const METADATA_TABLES = {
  source: {
    explanation: 'The TSS source: one row, with what holds for every field. IRIs are written in full; ' +
      "modified_at is when the source's metadata was last edited (its tss:modifiedAt).",
    columns: [
      ['source', 'VARCHAR'],
      ['label', 'VARCHAR'],
      ['sql_table', 'VARCHAR'],
      ['format', 'VARCHAR'],
      ['time_field', 'VARCHAR'],
      ['from_time', 'TIMESTAMPTZ'],
      ['until_time', 'TIMESTAMPTZ'],
      ['produced_by', 'VARCHAR'],
      ['mutable', 'BOOLEAN'],
      ['modified_at', 'TIMESTAMPTZ'],
    ],
  },
  fields: {
    explanation: 'The TSS fields: one row per field. IRIs are written in full; ' +
      "role is 'time', 'value', 'describes' (e.g. a quality code) or NULL; " +
      'describes is the IRI of the field it describes.',
    columns: [
      ['column_name', 'VARCHAR'],
      ['sql_table', 'VARCHAR'],
      ['field', 'VARCHAR'],
      ['label', 'VARCHAR'],
      ['role', 'VARCHAR'],
      ['datatype', 'VARCHAR'],
      ['unit', 'VARCHAR'],
      ['observed_property', 'VARCHAR'],
      ['sensor', 'VARCHAR'],
      ['feature_of_interest', 'VARCHAR'],
      ['describes', 'VARCHAR'],
    ],
  },
  summaries: {
    explanation: 'The stored summaries (tss:summary): one row per field that has one, computed from ' +
      'the data when the metadata was made (generated_at) and not refreshed since. ' +
      'They cover [from_time, until_time); value_count counts the values, missing the ' +
      'empty ones; the numbers are only there for numeric fields.',
    columns: [
      ['field', 'VARCHAR'],
      ['column_name', 'VARCHAR'],
      ['sql_table', 'VARCHAR'],
      ['from_time', 'TIMESTAMPTZ'],
      ['until_time', 'TIMESTAMPTZ'],
      ['missing_value_policy', 'VARCHAR'],
      ['value_count', 'BIGINT'],
      ['missing', 'BIGINT'],
      ['first_time', 'TIMESTAMPTZ'],
      ['last_time', 'TIMESTAMPTZ'],
      ['observed_minimum', 'DOUBLE'],
      ['observed_maximum', 'DOUBLE'],
      ['mean', 'DOUBLE'],
      ['median', 'DOUBLE'],
      ['generated_at', 'TIMESTAMPTZ'],
    ],
  },
}

// A text as a SQL string: 'like this'. A ' inside it is written twice (sql_box.sql_text).
export function sqlText(text) {
  return "'" + String(text).replaceAll("'", "''") + "'"
}

// A column or table name for in SQL (sql_box.sql_identifier). Simple names stay as they are;
// anything else, and words SQL uses itself (like order or end), gets "double quotes".
//   reservedWords: a Set of DuckDB's reserved words (db.reservedWords)
export function sqlIdentifier(name, reservedWords) {
  if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(name) && !reservedWords.has(name.toLowerCase())) return name
  return '"' + name.replaceAll('"', '""') + '"'
}

// A SQL table name made from a file name, e.g. .../waterinfo_289423042.csv -> waterinfo_289423042
// (sql_box.table_name)
export function tableName(location, reservedWords) {
  let name = location.split('?')[0].split('#')[0].split('/').pop().toLowerCase()
  name = name.replace(/\.(csv|parquet)$/u, '')
  name = name.replace(/[^a-z0-9_]/gu, '_')
  if (name === '' || !/^[a-z]/.test(name)) name = 't_' + name
  if (reservedWords.has(name) || Object.hasOwn(METADATA_TABLES, name)) name = name + '_data'
  return name
}

// The IRIs of the fields that have a stored summary (sql_routes.summarized_fields)
//   source: from readSource in sourceReading.js
export function summarizedFields(store, source) {
  const summarized = []
  for (const field of source.fields) {
    if (one(store, field.node, TSS_SUMMARY) !== null) summarized.push(field.node.value)
  }
  return summarized
}

// The SQL condition "this row of the data (d) lies in the window of the summary (s)" (sql_box.summary_window)
function summaryWindow(when, indent) {
  return `${indent}AND (s.from_time IS NULL OR d.${when} >= s.from_time)\n` +
    `${indent}AND (s.until_time IS NULL OR d.${when} < s.until_time)`
}

// A query that checks every stored summary against the data (sql_box.summary_check_sql)
//   t, when: the data table and its time column (both written for SQL)
//   columns: [[column name, field IRI], ...] of the columns that have a summary
function summaryCheckSql(t, when, columns, reservedWords) {
  const parts = []
  for (const [name, iri] of columns) {
    const c = sqlIdentifier(name, reservedWords)
    parts.push(
      `  SELECT ${sqlText(iri)} AS field,\n` +
      `         count(d.${c}) AS data_count,\n` +
      `         count(*) - count(d.${c}) AS data_missing,\n` +
      `         min(d.${when}) FILTER (WHERE d.${c} IS NOT NULL) AS data_first,\n` +
      `         max(d.${when}) FILTER (WHERE d.${c} IS NOT NULL) AS data_last\n` +
      `  FROM ${t} AS d, summaries AS s\n` +
      `  WHERE s.field = ${sqlText(iri)}\n` +
      summaryWindow(when, '    '),
    )
  }
  return '-- One row per column with a stored summary: what the summary says, and the same\n' +
    '-- numbers computed from the data now (only rows in the summary\'s window count)\n' +
    'WITH from_the_data AS (\n' + parts.join('\n  UNION ALL\n') + '\n)\n' +
    'SELECT s.column_name,\n' +
    '       s.value_count AS stored_count, d.data_count,\n' +
    '       s.missing AS stored_missing, d.data_missing,\n' +
    '       s.first_time AS stored_first, d.data_first,\n' +
    '       s.last_time AS stored_last, d.data_last,\n' +
    '       (s.value_count IS NULL OR s.value_count = d.data_count)\n' +
    '         AND (s.missing IS NULL OR s.missing = d.data_missing)\n' +
    '         AND (s.first_time IS NULL OR s.first_time = d.data_first)\n' +
    '         AND (s.last_time IS NULL OR s.last_time = d.data_last) AS up_to_date\n' +
    'FROM summaries AS s JOIN from_the_data AS d USING (field)\n' +
    'ORDER BY up_to_date, s.column_name'
}

// A few queries to start from, made for this source's table and fields (sql_box.example_queries).
// Each is { title, sql, field }: field is the IRI of the field it is about, or null.
//   table: the data table (null when it can't be loaded); fields: readSource(...).fields;
//   summarized: the IRIs of the fields with a stored summary
export function exampleQueries(table, fields, summarized, reservedWords) {
  const examples = [{
    title: 'Which column is which field (the TSS metadata)',
    sql: 'SELECT column_name, role, datatype, unit, observed_property\nFROM fields',
    field: null,
  }]
  if (summarized.length > 0) {
    examples.push({
      title: 'The stored summaries (tss:summary in the metadata)',
      sql: 'SELECT column_name, value_count, missing, first_time, last_time,\n' +
        '       observed_minimum, observed_maximum, round(mean, 3) AS mean, median,\n' +
        '       generated_at\n' +
        'FROM summaries\nORDER BY column_name',
      field: null,
    })
  }
  if (table === null) return examples

  const t = sqlIdentifier(table.name, reservedWords)
  examples.unshift({ title: 'The first 10 rows of the data', sql: `SELECT *\nFROM ${t}\nLIMIT 10`, field: null })
  examples.push({ title: 'Summary of every column of the data', sql: `SUMMARIZE ${t}`, field: null })

  // The examples below need the time column and numeric value columns in the table
  const types = new Map()
  for (const column of table.columns) types.set(column.name, column.type)
  let timeColumn = null
  const measured = [] // fields with an observed property that don't describe another field, with numbers in them
  for (const field of fields) {
    if (!types.has(field.columnName)) continue
    if (field.isTime) {
      timeColumn = field.columnName
    } else if (field.observedProperty !== null && field.describes === null &&
        NUMERIC_TYPES.includes(types.get(field.columnName))) {
      measured.push(field)
    }
  }

  // Every column with a stored summary, checked against the data (needs the time column)
  const checked = []
  for (const field of fields) {
    if (summarized.includes(field.node.value) && types.has(field.columnName)) {
      checked.push([field.columnName, field.node.value])
    }
  }
  if (timeColumn !== null && checked.length > 0) {
    examples.push({
      title: 'Is every stored summary still up to date? (count, missing, first and last time)',
      sql: summaryCheckSql(t, sqlIdentifier(timeColumn, reservedWords), checked, reservedWords),
      field: null,
    })
  }

  if (timeColumn === null || measured.length === 0) return examples

  const first = measured[0]
  const firstIri = first.node.value // the examples about the first series name its field by IRI
  const when = sqlIdentifier(timeColumn, reservedWords)
  const value = sqlIdentifier(first.columnName, reservedWords)

  examples.push({
    title: 'Daily mean, min and max',
    sql: `SELECT date_trunc('day', ${when}) AS day,\n` +
      `       round(avg(${value}), 3) AS mean,\n` +
      `       min(${value}) AS min,\n` +
      `       max(${value}) AS max,\n` +
      `       count(${value}) AS readings\n` +
      `FROM ${t}\nGROUP BY ALL\nORDER BY day`,
    field: firstIri,
  })
  examples.push({
    title: 'Moving average over the last 4 readings',
    sql: `SELECT ${when}, ${value},\n` +
      `       round(avg(${value}) OVER (ORDER BY ${when} ROWS BETWEEN 3 PRECEDING AND CURRENT ROW), 3) AS moving_avg\n` +
      `FROM ${t}\nORDER BY ${when}\nLIMIT 100`,
    field: firstIri,
  })
  examples.push({
    title: 'The 10 longest gaps between readings',
    sql: `SELECT lag(${when}) OVER (ORDER BY ${when}) AS previous_reading,\n` +
      `       ${when} AS next_reading,\n` +
      `       ${when} - lag(${when}) OVER (ORDER BY ${when}) AS gap\n` +
      `FROM ${t}\nORDER BY gap DESC NULLS LAST\nLIMIT 10`,
    field: null,
  })
  examples.push({
    title: "Only the readings inside the source's time window (data + metadata)",
    sql: `SELECT d.${when}, d.${value}\n` +
      `FROM ${t} AS d, source AS s\n` +
      `WHERE d.${when} >= s.from_time AND d.${when} < s.until_time\n` +
      `ORDER BY d.${when}\nLIMIT 100`,
    field: firstIri,
  })

  // The stored summary of the first series next to the same numbers computed from the data now
  if (summarized.includes(firstIri)) {
    examples.push({
      title: 'Stored summary next to the same numbers computed from the data',
      sql: "SELECT 'stored (tss:summary)' AS summary, value_count, missing, first_time, last_time,\n" +
        '       observed_minimum AS minimum, observed_maximum AS maximum,\n' +
        '       round(mean, 3) AS mean, round(median, 3) AS median\n' +
        'FROM summaries\n' +
        `WHERE field = ${sqlText(firstIri)}\n` +
        'UNION ALL\n' +
        `SELECT 'from the data now', count(d.${value}), count(*) - count(d.${value}),\n` +
        `       min(d.${when}) FILTER (WHERE d.${value} IS NOT NULL),\n` +
        `       max(d.${when}) FILTER (WHERE d.${value} IS NOT NULL),\n` +
        `       min(d.${value}), max(d.${value}), round(avg(d.${value}), 3), round(median(d.${value}), 3)\n` +
        `FROM ${t} AS d, summaries AS s\n` +
        `WHERE s.field = ${sqlText(firstIri)}\n` +
        summaryWindow(when, '  '),
      field: firstIri,
    })
  }

  // A field that describes the first value (e.g. a quality code): the readings per code.
  // This example is about the describing field (the code), so it carries that field's IRI.
  for (const field of fields) {
    if (field.describes !== null && field.describes.equals(first.node) && types.has(field.columnName)) {
      const code = sqlIdentifier(field.columnName, reservedWords)
      examples.push({
        title: 'Readings per value',
        sql: `SELECT ${code}, count(*) AS readings, round(avg(${value}), 3) AS mean\n` +
          `FROM ${t}\nGROUP BY ALL\nORDER BY readings DESC`,
        field: field.node.value,
      })
      break
    }
  }

  // Two series side by side: only when the table has a second measured field
  if (measured.length > 1) {
    const other = sqlIdentifier(measured[1].columnName, reservedWords)
    examples.push({
      title: 'Two series side by side, per day',
      sql: `SELECT date_trunc('day', ${when}) AS day,\n` +
        `       round(avg(${value}), 3) AS ${value},\n` +
        `       round(avg(${other}), 3) AS ${other}\n` +
        `FROM ${t}\nGROUP BY ALL\nORDER BY day`,
      field: null,
    })
  }
  return examples
}

// What the SQL box can query for this endpoint: the data table, the metadata tables, and example
// queries. The same JSON as GET /api/endpoints/{id}/sql/tables.
//   db: from openDatabase (duck.js); store and source: rdf.store and rdf.source
export function sqlTables(db, store, source) {
  const info = readSource(store, source)
  const table = db.tables[0]

  const metadataTables = []
  for (const name of Object.keys(METADATA_TABLES)) {
    const columns = []
    for (const [columnName, sqlType] of METADATA_TABLES[name].columns) {
      columns.push({ name: columnName, type: sqlType })
    }
    metadataTables.push({ name: name, explanation: METADATA_TABLES[name].explanation, columns: columns })
  }

  return {
    tables: [{ name: table.name, error: table.error, columns: table.columns }],
    metadataTables: metadataTables,
    examples: exampleQueries(table.error === null ? table : null, info.fields,
      summarizedFields(store, info), db.reservedWords),
    maxRows: MAX_ROWS,
    maxSeconds: MAX_SECONDS,
  }
}

// Run one read-only SQL query on the endpoint's database. The same JSON as POST /api/endpoints/{id}/sql:
//   { columns, rows, moreRows, seconds, skipped: [{ name, error }] }
// A mistake in the query rejects the Promise with DuckDB's own message, and when the data table
// was not loaded, why (that may be why the query fails), as the backend does.
export function runSql(db, sql) {
  // The metadata could not become tables (e.g. a tss:from that is no date-time): no query can run
  if (db.error) return Promise.reject(new Error(db.error))
  return db.run(sql)
    .then((result) => {
      return {
        columns: result.columns,
        rows: result.rows,
        moreRows: result.moreRows,
        seconds: result.seconds,
        skipped: db.skipped, // the tables that could not be loaded, and why
      }
    })
    .catch((error) => {
      let message = error.message
      for (const skippedTable of db.skipped) {
        message += `\n(Table ${skippedTable.name} was not loaded: ${skippedTable.error})`
      }
      throw new Error(message)
    })
}
