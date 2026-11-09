// dataReading.js: the rows of the source's data, the way the backend reads them: a CSV file, a
// Parquet file (DuckDB reads it: db.parquetCells in duck.js) or the points embedded in the
// metadata (RDF nodes with tss:pointOf). eachDataRow gives every row; firstDataRows the first few,
// for the series inspector.
//
// It is a port of code/webapp/backend/data_reading.py (data_rows, data_columns, first_rows,
// point_rows, parquet_head, column_positions, csv_header, check_csv_settings, data_location).
// Used by query.js (the data routes) and duck.js (the SQL table). Reading the data gives
// Promises: async functions (see the note at the top of query.js).

import { pointStore, pointRows, TSS_CSV, TSS_PARQUET } from '../tss.js'
import { compareCodePoints, messageText, pyRepr } from './python.js'
import { literalTime, valueText } from './values.js'
import { hasFormat } from './sourceReading.js'
import { eachCsvRecord, firstCsvRecord } from './csvFile.js'

// Stop with the reason when this dashboard can't read the source's CSV file (data_reading.check_csv_settings)
export function checkCsvSettings(source) {
  if (Array.from(source.delimiter).length !== 1) {
    throw new Error(`tss:delimiter must be one character, not '${source.delimiter}'`)
  }
  if (!['utf-8', 'utf8'].includes(source.encoding.toLowerCase())) {
    throw new Error(`This dashboard only reads UTF-8 files, not tss:encoding '${source.encoding}'`)
  }
}

// Where the source's data is: its tss:dataFile, which must be there (data_reading.data_location)
function dataLocation(data, source) {
  if (source.dataFile === null) throw new Error('The source has no tss:dataFile')
  if (!data.dataFile) throw new Error(`Data file not found: ${source.dataFile}`)
  return source.dataFile
}

// Where each field's values are in the rows of a data file: field IRI -> column position
// (data_reading.column_positions). header: the column names, or null for a CSV file without
// a header line (then a field's column is its tss:columnIndex). only: a Set of field IRIs, or null.
function columnPositions(source, header, only) {
  const positions = new Map()
  for (const field of source.fields) {
    if (only !== null && !only.has(field.node.value)) continue
    if (header === null) {
      if (field.columnIndex !== null) positions.set(field.node.value, field.columnIndex)
    } else if (field.columnName !== null && header.includes(field.columnName)) {
      positions.set(field.node.value, header.indexOf(field.columnName))
    }
  }
  return positions
}

// Column names for a CSV file without a header line (data_reading.csv_header): each column is
// named after the field with that tss:columnIndex, the others "column 0", "column 1", ...
export function csvHeader(source, firstCells) {
  const names = []
  for (let index = 0; index < firstCells.length; index++) {
    let name = `column ${index}`
    for (const field of source.fields) {
      if (field.columnIndex === index && field.columnName !== null) name = field.columnName
    }
    names.push(name)
  }
  return names
}

// The embedded points as rows in time order (data_reading.point_rows): each row is an object
// field IRI -> its Literal. The points were read once by App.jsx (rdf.points, see readPoints
// in tss.js); when they are not there, they are read here.
export async function pointRowsOf(data, source) {
  let points = data.points
  if (!points) {
    points = { rows: pointRows(pointStore(data.store, source.node), data.store, source.node), error: null }
  }
  if (points.error) throw new Error(points.error)

  const fields = new Map()
  for (const field of source.fields) fields.set(field.node.value, field)
  const rows = []
  for (const point of points.rows) {
    const row = {}
    for (const iri of Object.keys(point.values)) {
      if (!fields.has(iri)) continue
      const value = point.values[iri]
      if (value.termType !== 'Literal') {
        const field = fields.get(iri)
        const name = field.columnName !== null ? field.columnName : messageText(field.node, data.prefixes)
        throw new Error(`A point has a node as its ${pyRepr(name)} value, not a literal.`)
      }
      row[iri] = value
    }
    if (Object.keys(row).length > 0) rows.push(row)
  }

  // Points have no order in RDF: by their time, and a point without a valid time last
  // (data_reading.point_time). Array sort is stable, like Python's.
  const timeIri = source.timeField !== null ? source.timeField.value : null
  const order = (row) => {
    const value = timeIri !== null ? row[timeIri] : undefined
    return value ? literalTime(value) : null
  }
  rows.sort((a, b) => {
    const timeA = order(a)
    const timeB = order(b)
    if (timeA === null && timeB === null) return 0
    if (timeA === null) return 1
    if (timeB === null) return -1
    return timeA - timeB
  })
  return rows
}

// The column names and the first rows of the Parquet file, each value as text the way
// data_reading.cell_text writes it (data_reading.parquet_head). DuckDB (duck.js) reads the file:
// its table holds the file's rows with the file's own types. limit: null for every row.
// A problem rejects with "Could not read the Parquet file <location>: <why>"; verb changes the
// start of that message (GET /data says "Could not preview Parquet file ...").
export async function parquetCells(data, location, limit, verb = 'read the') {
  try {
    if (!data.getDatabase) throw new Error('no DuckDB database was given (getDatabase)')
    const db = await data.getDatabase()
    return await db.parquetCells('preview', limit) // await: so a rejection is caught below
  } catch (error) {
    throw new Error(`Could not ${verb} Parquet file ${location}: ${error.message}`, { cause: error })
  }
}

// Every row of the source's data, one at a time (data_reading.data_rows): visit(row) gets an
// object field IRI -> its value, and may return false to stop.
//   CSV file:      the text of the cell, exactly as it is in the file
//   Parquet file:  the value as text (data_reading.cell_text)
//   embedded:      the point's Literal
// only: a Set of the field IRIs a route needs, or null for every field.
// Gives a Promise; a problem with the data rejects it with the reason.
export async function eachDataRow(data, source, only, visit) {
  if (source.embedded === true) {
    for (const row of await pointRowsOf(data, source)) {
      if (visit(row) === false) return
    }
    return
  }

  const location = dataLocation(data, source)

  if (hasFormat(source, TSS_PARQUET)) {
    const table = await parquetCells(data, location, null)
    const positions = columnPositions(source, table.names, only)
    for (const found of table.rows) {
      const row = {}
      for (const [iri, index] of positions) row[iri] = found[index]
      if (visit(row) === false) return
    }
    return
  }

  if (hasFormat(source, TSS_CSV)) {
    checkCsvSettings(source)
    let positions = null
    let first = true
    await eachCsvRecord(data.dataFile, source.delimiter, (cells) => {
      if (first) {
        first = false
        if (source.header) {
          positions = columnPositions(source, cells, only)
          return true
        }
        positions = columnPositions(source, null, only)
      }
      const row = {}
      for (const [iri, index] of positions) {
        if (index < cells.length) row[iri] = cells[index]
      }
      return visit(row)
    })
    return
  }

  throw new Error(`The dashboard can't read data in tss:format ${messageText(source.format, data.prefixes)}`)
}

// The fields that have a column in the data (their IRIs), in the order of the data's columns
// (data_reading.data_columns)
async function dataColumns(data, source) {
  if (source.embedded === true) {
    const columns = []
    for (const field of source.fields) {
      if (field.columnName !== null) columns.push(field.node.value)
    }
    return columns
  }

  const location = dataLocation(data, source)
  let positions
  if (hasFormat(source, TSS_PARQUET)) {
    const table = await parquetCells(data, location, 0)
    positions = columnPositions(source, table.names, null)
  } else if (hasFormat(source, TSS_CSV)) {
    checkCsvSettings(source)
    let header = null
    if (source.header) header = await firstCsvRecord(data.dataFile, source.delimiter)
    positions = columnPositions(source, header, null)
  } else {
    throw new Error(`The dashboard can't read data in tss:format ${messageText(source.format, data.prefixes)}`)
  }

  // The fields in the order of their columns (and by IRI for two fields in one column)
  const order = Array.from(positions.entries())
  order.sort((a, b) => (a[1] !== b[1] ? a[1] - b[1] : compareCodePoints(a[0], b[0])))
  return order.map((entry) => entry[0])
}

// The first rows of the data, for the series inspector (data_reading.first_rows): one column per
// field with a column in the data (see dataColumns), named by the field's IRI; each cell is the
// text of the value, or null (no value). Gives a Promise of { columns, rows }.
//   count: how many rows (the route keeps it between 1 and 1000)
export async function firstDataRows(data, source, count) {
  const columns = await dataColumns(data, source)
  const rows = []
  if (hasFormat(source, TSS_PARQUET) && source.embedded !== true) {
    // Only the first rows of a Parquet file are asked from DuckDB
    const location = dataLocation(data, source)
    const table = await parquetCells(data, location, count)
    const positions = columnPositions(source, table.names, null)
    for (const found of table.rows) {
      rows.push(columns.map((iri) => (found[positions.get(iri)] === '' ? null : found[positions.get(iri)])))
    }
    return { columns: columns, rows: rows }
  }
  await eachDataRow(data, source, null, (row) => {
    const cells = []
    for (const iri of columns) {
      const text = valueText(row, iri)
      cells.push(text === '' ? null : text)
    }
    rows.push(cells)
    return rows.length < count
  })
  return { columns: columns, rows: rows }
}
