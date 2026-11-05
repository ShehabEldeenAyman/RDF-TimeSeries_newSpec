// query.js: the backend's data routes, run in the browser, for endpoints whose data must stay in
// this tab (an uploaded or online user endpoint). The data never goes to the server.
//
// It is a port of the backend's Python, with the same rules and the same JSON answers:
//   GET /data            -> dataPreview      (data_routes.get_data_file)
//   GET /rows            -> firstRows        (data_routes.get_first_rows, data_reading.first_rows)
//   GET /filter-columns  -> filterColumns    (data_routes.filter_columns)
//   GET /query           -> querySeries      (data_routes.query_series, query_helpers.py)
// The routes use the other files of the engine, each named after the Python it ports:
//   sourceReading.js  the tss:Source and its fields (source_reading.py)
//   dataReading.js    the rows of a CSV file, a Parquet file or the embedded points (data_reading.py)
//   csvFile.js        the lines and the CSV cells of a file (files.read_lines, Python's csv module)
//   values.js         one value as a time or a number (value_reading.py)
//   queryHelpers.js   the summary, gaps and hourly/daily values of a query (query_helpers.py)
//   python.js         Python's way of writing, rounding and adding numbers and texts
// duck.js, sqlBox.js and observationsWorker.js use those files too.
//
// Usage (data is the "rdf" prop every tab gets from App.jsx, with two more keys):
//   import { dataPreview, firstRows, filterColumns, querySeries } from './browser/query.js'
//
//   const data = { ...rdf, dataFile: file, getDatabase: getDatabase }
//     rdf:         { store, prefixes, source, points, ... }  (see App.jsx)
//     dataFile:    the tss:dataFile contents, a File or Blob (null: not there, or embedded points)
//     getDatabase: only needed for a Parquet file (DuckDB reads it): a function that gives a
//                  Promise of the database of duck.js (openDatabase), opened once per endpoint
//
//   dataPreview(data, 20).then((answer) => ...)       // { found, note, text }
//   firstRows(data, 10).then((answer) => ...)         // { columns: [field IRI], rows: [[text or null]] }
//   filterColumns(data, fieldIri).then((list) => ...) // [{ field, values }]
//   // params: the same names as the /query address, as URLSearchParams or as a plain object
//   querySeries(data, params).then((answer) => ...)   // { resample, aggregate, filterValue, summary, gaps, ... }
//   // A problem gives a rejected Promise with the same message the backend sends as "detail":
//   //   .catch((error) => setError(error.message))
//
// A note on the style: unlike the rest of the app, this file (and csvFile.js, dataReading.js,
// duck.js, sqlCheck.js, observationsWorker.js) uses async functions.
// "async function f()" gives a Promise, like the functions elsewhere; inside it, "await x" waits
// for the Promise x, like x.then(...) would, before the next line runs. Callers still use .then().
// Reading a file 1 MB at a time is easier to follow this way than as a long chain of .then.

import { TSS_CSV, TSS_PARQUET, XSD_DATE_TIME_STAMP } from '../tss.js'
import { compareCodePoints, messageText, pyFloatRepr, pyRepr, pyRound } from './python.js'
import {
  dataTime,
  isNumeric,
  literalNumber,
  literalTime,
  parseFormNumber,
  parseFormTime,
  readNumber,
  timeText,
  valueText,
  windowMoment,
} from './values.js'
import { findSourceField, hasFormat, readSource } from './sourceReading.js'
import { eachLine } from './csvFile.js'
import { eachDataRow, firstDataRows, parquetCells } from './dataReading.js'
import { findGaps, resampleReadings, summarize, textSummary } from './queryHelpers.js'

// ---------------------------------------------------------------------------
// The parameters of the address, read like FastAPI reads them
// ---------------------------------------------------------------------------

// A whole number from the address, like FastAPI reads an int parameter (limit, lines). A wrong
// one gets FastAPI's answer (a 422 with a list of problems), written as text like errorText does.
function parseWholeNumber(value, name, fallback) {
  if (value === null || value === undefined) return fallback
  if (typeof value === 'number' && Number.isInteger(value)) return value
  const text = String(value)
  if (/^\s*[+-]?[0-9]+\s*$/.test(text)) return Number(text)
  throw new Error(JSON.stringify([{
    type: 'int_parsing',
    loc: ['query', name],
    msg: 'Input should be a valid integer, unable to parse string as an integer',
    input: text,
  }]))
}

// ---------------------------------------------------------------------------
// GET /data: a preview of the data file
// ---------------------------------------------------------------------------

// A CSV line as Python's csv.writer writes it (lineterminator "\n"): a cell with the
// delimiter, a quote or a line break gets quotes around it, a quote inside is written twice.
// A line with one empty cell is written "" (otherwise it would be an empty line).
function csvLine(cells) {
  if (cells.length === 1 && cells[0] === '') return '""\n'
  const written = []
  for (const cell of cells) {
    if (/[,"\r\n]/.test(cell)) written.push('"' + cell.replaceAll('"', '""') + '"')
    else written.push(cell)
  }
  return written.join(',') + '\n'
}

// The preview of the data file (GET /data): the first lines of a CSV file as they are, or the
// first rows of a Parquet file written as CSV text. Gives a Promise of { found, note, text }.
//   lines: how many lines (1 to 1000; default 20)
export async function dataPreview(data, lines = 20) {
  const source = readSource(data.store, data.source)
  if (source.embedded === true) {
    throw new Error('This source has no data file: its points are embedded in the Turtle file (tss:pointOf).')
  }
  let count = parseWholeNumber(lines, 'lines', 20)
  count = Math.max(1, Math.min(count, 1000))
  const location = source.dataFile
  if (location === null) throw new Error('The source has no tss:dataFile.')
  if (!data.dataFile) return { found: false, note: 'The data file (tss:dataFile) was not found.', text: null }

  if (hasFormat(source, TSS_PARQUET)) {
    const table = await parquetCells(data, location, count - 1, 'preview') // count - 1: the header line is a line too
    let text = csvLine(table.names)
    for (const row of table.rows) text += csvLine(row)
    return {
      found: true,
      note: `CSV preview of the first ${table.rows.length} Parquet rows; download contains the original Parquet file`,
      text: text,
    }
  }
  if (!hasFormat(source, TSS_CSV)) {
    throw new Error('A data preview is available for tss:CSV and tss:Parquet sources only, ' +
      `not ${messageText(source.format, data.prefixes)}.`)
  }

  const firstLines = []
  await eachLine(data.dataFile, (text) => {
    firstLines.push(text)
    return firstLines.length < count
  })
  const sizeMb = pyFloatRepr(pyRound(data.dataFile.size / 1000000, 1))
  return {
    found: true,
    note: `first ${firstLines.length} lines of a ${sizeMb} MB file`,
    text: firstLines.join('\n'),
  }
}

// ---------------------------------------------------------------------------
// GET /rows: the first rows, for the series inspector
// ---------------------------------------------------------------------------

// The first rows of the data (GET /rows): one column per field with a column in the data,
// named by the field's IRI; each cell is the text of the value, or null (no value).
// Gives a Promise of { columns, rows }.
//   limit: how many rows (1 to 1000; default 10)
export async function firstRows(data, limit = 10) {
  const source = readSource(data.store, data.source)
  let count = parseWholeNumber(limit, 'limit', 10)
  count = Math.max(1, Math.min(count, 1000))
  return firstDataRows(data, source, count)
}

// ---------------------------------------------------------------------------
// GET /filter-columns: the other columns the query form can filter on
// ---------------------------------------------------------------------------

// The other columns the query form can filter on (like a quality code), with the values that
// occur in them: only columns with 2 to 30 different values. Gives a Promise of [{ field, values }].
//   field: the IRI of the field the query is about
export async function filterColumns(data, field) {
  const source = readSource(data.store, data.source)
  const chosen = findSourceField(source, field)

  // Start with every field except the time and this field, and drop a field as soon as it has
  // more than 30 different values
  const candidates = new Map() // field IRI -> the Set of values seen so far
  for (const other of source.fields) {
    if (other.columnName === null || other.isTime || other.node.equals(chosen.node)) continue
    candidates.set(other.node.value, new Set())
  }
  await eachDataRow(data, source, new Set(candidates.keys()), (row) => {
    for (const iri of Array.from(candidates.keys())) {
      const values = candidates.get(iri)
      values.add(valueText(row, iri))
      if (values.size > 30) candidates.delete(iri)
    }
    return candidates.size > 0
  })

  const columns = []
  for (const other of source.fields) {
    const values = candidates.get(other.node.value)
    if (values !== undefined && values.size >= 2) {
      columns.push({ field: other.node.value, values: Array.from(values).sort(compareCodePoints) })
    }
  }
  return columns
}

// ---------------------------------------------------------------------------
// GET /query: the readings of one field, with summary numbers and gaps (query_helpers.py)
// ---------------------------------------------------------------------------

// Read a parameter from URLSearchParams or from a plain object: null when it is not there
function parameter(params, name) {
  if (!params) return null
  if (typeof params.get === 'function') return params.get(name)
  const value = params[name]
  return value === undefined ? null : value
}

// The readings of one field (one time series), with the filters of the query form, plus summary
// numbers and the gaps in the data (GET /query). Gives a Promise of the same JSON as the route:
//   { resample, aggregate, filterValue, summary, gaps, totalRows, limitReached, readings }
// params: the parameters of the /query address, as URLSearchParams or a plain object:
//   field (required), start, end, min_value, max_value, filterField, filter_value, resample
//   ('', 'hour' or 'day'), aggregate ('mean', 'min' or 'max'), limit (default 100, at most 5000)
export async function querySeries(data, params) {
  // FastAPI checks the parameters before the route starts: field is required, limit a whole number
  const field = parameter(params, 'field')
  if (field === null) {
    throw new Error(JSON.stringify([{ type: 'missing', loc: ['query', 'field'], msg: 'Field required', input: null }]))
  }
  let limit = parseWholeNumber(parameter(params, 'limit'), 'limit', 100)
  const source = readSource(data.store, data.source)
  const chosen = findSourceField(source, field)
  limit = Math.max(1, Math.min(limit, 5000)) // don't make huge answers

  // 1. What the form asks for
  const start = parameter(params, 'start')
  const end = parameter(params, 'end')
  const startTime = start ? parseFormTime(start) : null
  const endTime = end ? parseFormTime(end) : null
  const low = parseFormNumber(parameter(params, 'min_value'), 'The lowest value')
  const high = parseFormNumber(parameter(params, 'max_value'), 'The highest value')
  const resampleParameter = parameter(params, 'resample')
  const resample = resampleParameter === null ? '' : resampleParameter
  const aggregateParameter = parameter(params, 'aggregate')
  const aggregate = aggregateParameter === null ? 'mean' : aggregateParameter
  if (!['', 'hour', 'day'].includes(resample)) throw new Error("resample must be empty, 'hour' or 'day'")
  if (!['mean', 'min', 'max'].includes(aggregate)) throw new Error("aggregate must be 'mean', 'min' or 'max'")

  // 2. The fields to read: the time field, this field, and the field to filter on
  let timeField = null
  for (const other of source.fields) {
    if (other.isTime) timeField = other
  }
  if (timeField === null) {
    throw new Error('The source has no tss:timeField (one of its fields), so the readings have no time.')
  }
  if (chosen.isTime) throw new Error('This is the time field itself. Pick a field that holds values.')
  const filterField = parameter(params, 'filterField')
  const filterValue = parameter(params, 'filter_value')
  let filtered = null
  if (filterField) {
    for (const other of source.fields) {
      if (other.node.value === filterField) filtered = other
    }
    if (filtered === null) throw new Error(`filterField <${filterField}> is not a field of this source`)
    if (filterValue === null) throw new Error('filterField needs a filter_value too')
  }

  const datatype = chosen.datatype
  const numeric = isNumeric(datatype)
  if (!numeric && (low !== null || high !== null || resample)) {
    const holds = datatype !== null ? messageText(datatype, data.prefixes) : 'values without a tss:datatype'
    throw new Error(`Value ranges and mean/min/max aggregation need a numeric field. This field holds ${holds}.`)
  }

  // 3. The source only covers its own time window: from is included, until is not
  const windowStart = windowMoment(data.store, source.node, 'from', data.prefixes)
  const windowEnd = windowMoment(data.store, source.node, 'until', data.prefixes)

  // 4. Go through the data row by row
  const moments = [] // the time of every row inside the time range: to find the gaps
  const kept = []    // the readings that also pass the column filter and the value range
  const needed = [timeField, chosen]
  if (filtered !== null) needed.push(filtered)
  const only = new Set(needed.map((other) => other.node.value))
  const timeIri = timeField.node.value
  let first = true
  await eachDataRow(data, source, only, (row) => {
    // The columns must be in the data file (an embedded point may leave out an empty value)
    if (first && source.embedded !== true) {
      for (const other of needed) {
        if (row[other.node.value] === undefined) {
          const name = other.columnName || messageText(other.node, data.prefixes)
          throw new Error(`Column '${name}' is not in ${source.dataFile}`)
        }
      }
    }
    first = false

    // The time: an embedded point's literal, or the text of a cell
    const timeValue = row[timeIri] === undefined ? '' : row[timeIri]
    if (timeValue === '') throw new Error(`A row has no time in column '${timeField.columnName}'`)
    const rowTime = typeof timeValue === 'string' ? dataTime(timeValue, timeField.datatype) : literalTime(timeValue)
    if (rowTime === null) {
      let kind = 'an xsd:dateTime'
      if (timeField.datatype !== null && timeField.datatype.value === XSD_DATE_TIME_STAMP) {
        kind = 'an xsd:dateTimeStamp (a date-time with a timezone)'
      }
      const text = typeof timeValue === 'string' ? timeValue : timeValue.value
      throw new Error(`A row has ${pyRepr(text)} in column '${timeField.columnName}' ` +
        `(the tss:timeField), which is not ${kind} like 2021-03-03T23:45:00Z`)
    }
    if (windowStart !== null && rowTime < windowStart) return true
    if (windowEnd !== null && rowTime >= windowEnd) return true
    if (startTime !== null && rowTime < startTime) return true
    if (endTime !== null && rowTime > endTime) return true
    moments.push(rowTime)

    if (filtered !== null && valueText(row, filtered.node.value) !== filterValue) return true
    const cell = row[chosen.node.value] === undefined ? '' : row[chosen.node.value]
    let value
    if (numeric && typeof cell !== 'string') value = literalNumber(cell, datatype, data.prefixes)
    else if (numeric) value = readNumber(cell, datatype, data.prefixes)
    else {
      const text = valueText(row, chosen.node.value)
      value = text !== '' ? text : null
    }
    if (value === null && (low !== null || high !== null)) return true // with a value range, an empty cell never matches
    if (low !== null && value < low) return true
    if (high !== null && value > high) return true
    kept.push({ moment: rowTime, value: value })
    return true
  })

  // Files are usually in time order, but nothing guarantees it (a stable sort, like Python's)
  moments.sort((a, b) => a - b)
  kept.sort((a, b) => a.moment - b.moment)

  // 5. The rows to show: every reading, or one value per hour or per day
  let rows
  let totalRows
  if (resample) {
    rows = resampleReadings(kept, resample, aggregate)
    totalRows = rows.length
  } else {
    rows = []
    for (const reading of kept.slice(0, limit)) rows.push({ time: timeText(reading.moment), value: reading.value })
    totalRows = kept.length
  }

  return {
    resample: resample,
    aggregate: aggregate,
    filterValue: filtered !== null ? filterValue : null,
    summary: numeric ? summarize(kept) : textSummary(kept),
    gaps: findGaps(moments), // over every reading in the time range
    totalRows: totalRows,
    limitReached: totalRows > limit,
    readings: rows.slice(0, limit),
  }
}
