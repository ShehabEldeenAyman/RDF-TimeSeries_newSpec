// values.js: reading one value the way the backend does: the text of a data cell, or the literal
// of an embedded point (or of tss:from / tss:until), after the check that it has the XSD form of
// its datatype. Gives times and numbers.
//
// It is a port of code/webapp/backend/value_reading.py, with what it needs from elsewhere: the
// XSD forms (LEXICAL_FORMS of code/TSS2RDF/tss2rdf.py), the DuckDB type of each datatype
// (sql_box.XSD_TO_DUCKDB), and the times and numbers typed in the query form
// (query_helpers.parse_time, parse_number and time_text), which the converter needs too.
// Used by sourceReading.js, dataReading.js, queryHelpers.js, query.js, arrowValues.js, duck.js,
// sqlBox.js and observationsWorker.js.
//
// Times: a moment is a number of microseconds since 1970-01-01T00:00:00Z (Python keeps
// microseconds too; a JavaScript Date only keeps milliseconds). Such numbers are exact for
// every time with whole seconds, and for fractions of a second from about 1685 to 2255.

import { one, TSS, XSD, XSD_BOOLEAN, XSD_DATE_TIME, XSD_DATE_TIME_STAMP } from '../tss.js'
import { messageText, pyInt, pyRepr } from './python.js'

// ---------------------------------------------------------------------------
// XSD forms and DuckDB types (code/TSS2RDF/tss2rdf.py LEXICAL_FORMS, backend/sql_box.py XSD_TO_DUCKDB)
// ---------------------------------------------------------------------------

// How a value must look for each datatype (XSD lexical forms), the same patterns as
// LEXICAL_FORMS in code/TSS2RDF/tss2rdf.py. A datatype not in here accepts any text.
const INTEGER = '[+-]?[0-9]+'
const DECIMAL = '[+-]?([0-9]+(\\.[0-9]*)?|\\.[0-9]+)'
const DOUBLE = '[+-]?(([0-9]+(\\.[0-9]*)?|\\.[0-9]+)([eE][+-]?[0-9]+)?|INF)|NaN'
const DATE = '-?[0-9]{4,}-[0-9]{2}-[0-9]{2}'
const DATE_TIME = DATE + 'T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]+)?'
const TIMEZONE = '(Z|[+-][0-9]{2}:[0-9]{2})'
export const LEXICAL_FORMS = {
  [XSD + 'integer']: INTEGER,
  [XSD + 'int']: INTEGER,
  [XSD + 'long']: INTEGER,
  [XSD + 'short']: INTEGER,
  [XSD + 'byte']: INTEGER,
  [XSD + 'nonNegativeInteger']: '\\+?[0-9]+',
  [XSD + 'positiveInteger']: '\\+?[0-9]*[1-9][0-9]*',
  [XSD + 'nonPositiveInteger']: '(-[0-9]+|\\+?0+)',
  [XSD + 'negativeInteger']: '-[0-9]*[1-9][0-9]*',
  [XSD + 'unsignedLong']: '\\+?[0-9]+',
  [XSD + 'unsignedInt']: '\\+?[0-9]+',
  [XSD + 'unsignedShort']: '\\+?[0-9]+',
  [XSD + 'unsignedByte']: '\\+?[0-9]+',
  [XSD + 'decimal']: DECIMAL,
  [XSD + 'double']: DOUBLE,
  [XSD + 'float']: DOUBLE,
  [XSD + 'boolean']: 'true|false|1|0',
  [XSD + 'dateTime']: DATE_TIME + TIMEZONE + '?', // the timezone may be left out
  [XSD + 'dateTimeStamp']: DATE_TIME + TIMEZONE,  // an xsd:dateTime that must have a timezone
  [XSD + 'date']: DATE + TIMEZONE + '?',
}

// The same forms as regular expressions that must match the WHOLE text (Python's re.fullmatch):
// ^(?: ... )$ around each pattern (value_reading.DATA_FORMS)
const DATA_FORMS = {}
for (const datatype of Object.keys(LEXICAL_FORMS)) {
  DATA_FORMS[datatype] = new RegExp('^(?:' + LEXICAL_FORMS[datatype] + ')$')
}

// Does a text have the XSD form of a datatype (an IRI as text)? true for a datatype without a form.
export function hasForm(text, datatypeIri) {
  const form = DATA_FORMS[datatypeIri]
  return form === undefined || form.test(text)
}

// The datatypes a time (a value of the tss:timeField) may have
export const TIME_DATATYPES = [XSD_DATE_TIME, XSD_DATE_TIME_STAMP]

// A field's tss:datatype (the full IRI) -> the DuckDB column type (sql_box.XSD_TO_DUCKDB).
// Anything not in here stays text (VARCHAR).
export const XSD_TO_DUCKDB = {
  [XSD + 'dateTime']: 'TIMESTAMPTZ',
  [XSD + 'dateTimeStamp']: 'TIMESTAMPTZ',
  [XSD + 'date']: 'DATE',
  [XSD + 'time']: 'TIME',
  [XSD + 'double']: 'DOUBLE',
  [XSD + 'float']: 'DOUBLE',
  [XSD + 'decimal']: 'DOUBLE',
  [XSD + 'integer']: 'BIGINT',
  [XSD + 'int']: 'BIGINT',
  [XSD + 'long']: 'BIGINT',
  [XSD + 'short']: 'BIGINT',
  [XSD + 'byte']: 'BIGINT',
  [XSD + 'nonNegativeInteger']: 'BIGINT',
  [XSD + 'positiveInteger']: 'BIGINT',
  [XSD + 'nonPositiveInteger']: 'BIGINT',
  [XSD + 'negativeInteger']: 'BIGINT',
  [XSD + 'unsignedLong']: 'BIGINT',
  [XSD + 'unsignedInt']: 'BIGINT',
  [XSD + 'unsignedShort']: 'BIGINT',
  [XSD + 'unsignedByte']: 'BIGINT',
  [XSD + 'boolean']: 'BOOLEAN',
}

// The DuckDB types of numeric columns: only those have mean/min/max and value ranges
export const NUMERIC_TYPES = ['DOUBLE', 'BIGINT']

// The DuckDB type for a datatype (a term, or null): no datatype, or an unknown one, stays text
export function duckdbType(datatype) {
  if (datatype === null || datatype.termType !== 'NamedNode') return 'VARCHAR'
  const type = XSD_TO_DUCKDB[datatype.value]
  return type === undefined ? 'VARCHAR' : type
}

// Is this datatype numeric (value_reading.is_numeric)? Only numeric fields have value ranges and mean/min/max.
export function isNumeric(datatype) {
  return NUMERIC_TYPES.includes(duckdbType(datatype))
}

// ---------------------------------------------------------------------------
// Times (value_reading.data_time / literal_time, query_helpers.parse_time / time_text)
// ---------------------------------------------------------------------------

const DAYS_IN_MONTH = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

function isLeapYear(year) {
  return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0
}

// Microseconds since 1970 for a date and time in UTC, or null when it does not exist in
// Python's datetime (years 1 to 9999, a real day, hours 0-23, minutes and seconds 0-59)
function utcMicroseconds(year, month, day, hour, minute, second, microsecond) {
  if (year < 1 || year > 9999 || month < 1 || month > 12) return null
  const days = month === 2 && isLeapYear(year) ? 29 : DAYS_IN_MONTH[month - 1]
  if (day < 1 || day > days || hour > 23 || minute > 59 || second > 59) return null
  // new Date(0) + setUTCFullYear: Date.UTC would read the years 0-99 as 1900-1999
  const date = new Date(0)
  date.setUTCFullYear(year, month - 1, day)
  date.setUTCHours(hour, minute, second, 0)
  return date.getTime() * 1000 + microsecond
}

// The microseconds of a fraction of a second, like Python: '.5' -> 500000, '.1234567' -> 123456
function fractionMicroseconds(fraction) {
  if (!fraction) return 0
  return Number((fraction.slice(1) + '000000').slice(0, 6))
}

// A timezone ('Z', '+02:00', '-05:30') as minutes east of UTC, or null when Python refuses it
function timezoneMinutes(zone) {
  if (zone === 'Z') return 0
  const sign = zone[0] === '-' ? -1 : 1
  const hours = Number(zone.slice(1, 3))
  const minutes = Number(zone.slice(4, 6))
  if (hours > 23 || minutes > 59) return null
  return sign * (hours * 60 + minutes)
}

// The parts of an xsd:dateTime text (its form was checked before)
const XSD_DATE_TIME_PARTS = /^(-?[0-9]{4,})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})?$/

// A date-time text in the xsd:dateTime form as microseconds since 1970 (UTC), the way Python's
// datetime.fromisoformat reads it (rdflib reads time literals with it too). A time without a
// timezone is UTC. null when Python can't read it (30 February, year 0, 24:00:00, ...).
export function isoMoment(text) {
  const parts = XSD_DATE_TIME_PARTS.exec(text)
  if (parts === null || parts[1].length !== 4 || parts[1][0] === '-') return null
  const local = utcMicroseconds(Number(parts[1]), Number(parts[2]), Number(parts[3]),
    Number(parts[4]), Number(parts[5]), Number(parts[6]), fractionMicroseconds(parts[7]))
  if (local === null) return null
  const offset = parts[8] ? timezoneMinutes(parts[8]) : 0
  if (offset === null) return null
  return local - offset * 60 * 1000000
}

// The XSD form a time of this datatype (an IRI as text, or null) must have (value_reading.time_form)
function timeForm(datatypeIri) {
  return TIME_DATATYPES.includes(datatypeIri) ? datatypeIri : XSD_DATE_TIME
}

// The text of a time cell as a moment, or null when it is not an xsd:dateTime like
// 2021-03-03T23:45:00Z (value_reading.data_time). datatype: the time field's tss:datatype (a term or null)
export function dataTime(text, datatype) {
  const datatypeIri = datatype && datatype.termType === 'NamedNode' ? datatype.value : XSD_DATE_TIME
  if (!hasForm(text, timeForm(datatypeIri))) return null
  return isoMoment(text)
}

// A time literal (an embedded point's time, tss:from/tss:until, a summary time) as a moment,
// or null when it is not an xsd:dateTime or xsd:dateTimeStamp literal in the XSD form
// (value_reading.literal_time)
export function literalTime(literal) {
  if (!literal || literal.termType !== 'Literal') return null
  const datatypeIri = literal.datatype.value
  if (!TIME_DATATYPES.includes(datatypeIri) || !hasForm(literal.value, datatypeIri)) return null
  return isoMoment(literal.value)
}

// Split microseconds since 1970 into whole milliseconds (for a Date) and the microseconds left
function splitMicroseconds(moment) {
  const milliseconds = Math.floor(moment / 1000)
  return { milliseconds: milliseconds, micro: moment - milliseconds * 1000 }
}

// A moment as Python's isoformat writes it, without a timezone: 2021-03-04T01:00:00, with
// .ffffff only when there are microseconds
export function isoText(moment) {
  const parts = splitMicroseconds(moment)
  return isoTextParts(parts.milliseconds, parts.micro)
}

// The same, from whole milliseconds since 1970 and the microseconds (0-999) after them
export function isoTextParts(milliseconds, micro) {
  const date = new Date(milliseconds)
  const text = String(date.getUTCFullYear()).padStart(4, '0') + '-' +
    String(date.getUTCMonth() + 1).padStart(2, '0') + '-' +
    String(date.getUTCDate()).padStart(2, '0') + 'T' +
    String(date.getUTCHours()).padStart(2, '0') + ':' +
    String(date.getUTCMinutes()).padStart(2, '0') + ':' +
    String(date.getUTCSeconds()).padStart(2, '0')
  const fraction = date.getUTCMilliseconds() * 1000 + micro
  return fraction === 0 ? text : text + '.' + String(fraction).padStart(6, '0')
}

// A moment as text, always in UTC, e.g. 2021-03-04T01:00:00Z (query_helpers.time_text)
export function timeText(moment) {
  return isoText(moment) + 'Z'
}

// A date-time typed in the query form, e.g. 2021-03-04T00:00 (query_helpers.parse_time, which
// uses Python's datetime.fromisoformat). Without a timezone it is UTC. The parts:
//   [1] year [2] '-' or '' [3] month [4] day, then after any one separator character:
//   [5] hour [6] ':' or '' [7] minute [8] second [9] fraction, [10] the timezone
// (the \2 and \6 make the date, and the time, use one style: 2021-03-04 or 20210304)
const FORM_TIME = new RegExp('^([0-9]{4})(-?)([0-9]{2})\\2([0-9]{2})' +
  '(?:.([0-9]{2})(?:(:?)([0-9]{2})(?:\\6([0-9]{2})([.,][0-9]+)?)?)?' +
  '(Z|[+-][0-9]{2}(?::?[0-9]{2}(?::?[0-9]{2}(?:[.,][0-9]+)?)?)?)?)?$', 'u')
export function parseFormTime(text) {
  const parts = FORM_TIME.exec(text)
  const fail = () => new Error(`Not a valid date-time: '${text}' (expected something like 2021-03-04T00:00)`)
  if (parts === null) throw fail()
  const fraction = parts[9] ? '.' + parts[9].slice(1) : undefined
  const local = utcMicroseconds(Number(parts[1]), Number(parts[3]), Number(parts[4]),
    Number(parts[5] || 0), Number(parts[7] || 0), Number(parts[8] || 0), fractionMicroseconds(fraction))
  if (local === null) throw fail()
  let offset = 0
  if (parts[10] && parts[10] !== 'Z') {
    const digits = parts[10].slice(1).replace(/[:.,]/g, '')
    const sign = parts[10][0] === '-' ? -1 : 1
    const hours = Number(digits.slice(0, 2))
    const minutes = Number(digits.slice(2, 4) || 0)
    const seconds = Number(digits.slice(4, 6) || 0)
    if (hours > 23 || minutes > 59 || seconds > 59) throw fail()
    offset = sign * (hours * 3600 + minutes * 60 + seconds) * 1000000
  }
  return local - offset
}

// A number typed in the form, or null when it was left empty (query_helpers.parse_number,
// which reads it with Python's float(): spaces around it and _ between digits are fine)
const PYTHON_FLOAT = /^[+-]?(?:(?:[0-9](?:_?[0-9])*)(?:\.(?:[0-9](?:_?[0-9])*)?)?|\.[0-9](?:_?[0-9])*)(?:[eE][+-]?[0-9](?:_?[0-9])*)?$/
export function parseFormNumber(text, what) {
  if (text === null || text === undefined || text === '') return null
  const trimmed = String(text).trim()
  const special = /^([+-]?)(inf|infinity|nan)$/i.exec(trimmed)
  if (special !== null) {
    if (special[2].toLowerCase() === 'nan') return NaN
    return special[1] === '-' ? -Infinity : Infinity
  }
  if (!PYTHON_FLOAT.test(trimmed)) throw new Error(`${what} must be a number, not '${text}'`)
  return Number(trimmed.replaceAll('_', ''))
}

// ---------------------------------------------------------------------------
// Numbers (value_reading.read_number / literal_number)
// ---------------------------------------------------------------------------

// A numeric XSD text as a number, as Python reads it: int(text) for whole-number datatypes,
// float(text) for the others ("INF" -> Infinity, "NaN" -> NaN). The form was checked before.
function readNumberText(text, datatype) {
  if (duckdbType(datatype) === 'BIGINT') return pyInt(text)
  return parseFormNumber(text, 'value')
}

function declaresNumbers(text, datatype, prefixes) {
  return new Error(`The field declares numeric values (${messageText(datatype, prefixes)}), but contains ${pyRepr(text)}.`)
}

// The text of a numeric field's value as a number, or null when it is empty (value_reading.read_number)
export function readNumber(text, datatype, prefixes) {
  if (text === '') return null
  const form = datatype ? DATA_FORMS[datatype.value] : undefined
  if (form !== undefined && !form.test(text)) throw declaresNumbers(text, datatype, prefixes)
  let value
  try {
    value = readNumberText(text, datatype)
  } catch {
    value = null
  }
  if (value === null) throw declaresNumbers(text, datatype, prefixes)
  if (!Number.isFinite(value)) throw new Error('The field contains a non-finite numeric value.')
  return value
}

// The value of an embedded point (a Literal) for a numeric field (value_reading.literal_number):
// it must have the field's datatype, and its text the XSD form of that datatype
export function literalNumber(literal, datatype, prefixes) {
  const form = datatype ? DATA_FORMS[datatype.value] : undefined
  const sameType = datatype !== null && literal.datatype.value === datatype.value
  const wrongForm = form !== undefined && !form.test(literal.value)
  let value = null
  if (sameType && !wrongForm) value = pythonNumber(literal)
  if (value === null) throw declaresNumbers(literal.value, datatype, prefixes)
  if (!Number.isFinite(value)) throw new Error('The field contains a non-finite numeric value.')
  return value
}

// What rdflib's Literal.toPython() gives for a numeric literal, as a JavaScript number, or null
// when it is not a number (rdflib gives the literal itself back, or a bool)
export function pythonNumber(literal) {
  const datatypeIri = literal.datatype.value
  const type = XSD_TO_DUCKDB[datatypeIri]
  if (type === 'BIGINT') return pyInt(literal.value)
  if (type === 'DOUBLE') {
    // float(text); an xsd:decimal is read as a Decimal, which the backend turns into the same float
    try {
      return parseFormNumber(literal.value, 'value')
    } catch {
      return null
    }
  }
  return null
}

// A whole-number literal's value as exact decimal text (Python's int keeps every digit), or null
export function pythonIntegerText(literal) {
  if (pyInt(literal.value) === null) return null
  let text = literal.value.trim().replaceAll('_', '')
  let sign = ''
  if (text[0] === '+' || text[0] === '-') {
    sign = text[0] === '-' ? '-' : ''
    text = text.slice(1)
  }
  text = text.replace(/^0+(?=[0-9])/, '')
  return text === '0' ? '0' : sign + text
}

// What rdflib's toPython() gives for an xsd:boolean literal: true for "true" or "1" (in any
// case), false for anything else. null when the literal is no xsd:boolean.
export function pythonBoolean(term) {
  if (!term || term.termType !== 'Literal' || term.datatype.value !== XSD_BOOLEAN) return null
  const text = term.value.toLowerCase()
  return text === 'true' || text === '1'
}

// ---------------------------------------------------------------------------
// A field's value in a row, and the source's time window (value_text, window_moment)
// ---------------------------------------------------------------------------

// The text of a field's value in a row: a cell's text, or a point literal's text; '' when the
// row has no value for the field (value_reading.value_text)
export function valueText(row, fieldIri) {
  const value = row[fieldIri]
  if (value === undefined) return ''
  return typeof value === 'string' ? value : value.value
}

// tss:from or tss:until of the source as a moment, or null when it is not stated. It must be an
// xsd:dateTime literal (value_reading.window_moment).
export function windowMoment(store, sourceNode, name, prefixes) {
  const value = one(store, sourceNode, TSS + name)
  if (value === null) return null
  let moment = null
  if (value.termType === 'Literal' && value.datatype.value === XSD_DATE_TIME) moment = literalTime(value)
  if (moment === null) {
    throw new Error(`tss:${name} must be an xsd:dateTime like "2021-03-03T23:45:00Z"^^xsd:dateTime, ` +
      `not ${messageText(value, prefixes)}.`)
  }
  return moment
}
