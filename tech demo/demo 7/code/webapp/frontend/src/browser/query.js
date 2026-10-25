// query.js: the backend's data routes, run in the browser, for endpoints whose data must stay in
// this tab (an uploaded or online user endpoint). The data never goes to the server.
//
// It is a port of the backend's Python, with the same rules and the same JSON answers:
//   GET /data            -> dataPreview      (data_routes.get_data_file)
//   GET /rows            -> firstRows        (data_routes.get_first_rows, data_reading.first_rows)
//   GET /filter-columns  -> filterColumns    (data_routes.filter_columns)
//   GET /query           -> querySeries      (data_routes.query_series, query_helpers.py)
// plus what those need: reading the source (source_reading.read_source), the rows of a CSV file,
// a Parquet file or the embedded points (data_reading.py), and one value as a time or a number
// (value_reading.py). duck.js, sqlBox.js and observationsWorker.js use these helpers too.
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
// Times: a moment is a number of microseconds since 1970-01-01T00:00:00Z (Python keeps
// microseconds too; a JavaScript Date only keeps milliseconds). Such numbers are exact for
// every time with whole seconds, and for fractions of a second from about 1685 to 2255.
//
// A note on the style: unlike the rest of the app, this file (and duck.js, observationsWorker.js)
// uses async functions.
// "async function f()" gives a Promise, like the functions elsewhere; inside it, "await x" waits
// for the Promise x, like x.then(...) would, before the next line runs. Callers still use .then().
// Reading a file 1 MB at a time is easier to follow this way than as a long chain of .then.

import {
  all,
  one,
  RDFS_LABEL,
  TSS,
  TSS_COLUMN_INDEX,
  TSS_COLUMN_NAME,
  TSS_CSV,
  TSS_DATA_FILE,
  TSS_DATATYPE,
  TSS_DELIMITER,
  TSS_DESCRIBES_FIELD,
  TSS_EMBEDDED,
  TSS_ENCODING,
  TSS_FORMAT,
  TSS_HAS_FIELD,
  TSS_HEADER,
  TSS_OBSERVED_PROPERTY,
  TSS_PARQUET,
  TSS_TIME_FIELD,
  TSS_UNIT,
  XSD,
  XSD_BOOLEAN,
  XSD_DATE_TIME,
  XSD_DATE_TIME_STAMP,
  liftPoints,
  pointRows,
} from '../tss.js'

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
// Python's way of writing values, so the answers have the same text as the backend's
// ---------------------------------------------------------------------------
// Why: the browser's answers for your own endpoint must be the server's answers, character for
// character (the same JSON, so a test can compare the two as text). JavaScript rounds, writes and
// adds some numbers differently from Python:
//   round(0.0625, 3) is 0.062 in Python; Number((0.0625).toFixed(3)) is 0.063 in JavaScript
//   Python writes the float 1605.0; JavaScript writes 1605
//   Python's sum() of floats corrects its rounding errors (Neumaier); a JavaScript loop does not
// The functions below copy Python's way. You can skip them to follow what the app does.

// A float as Python writes it (repr): 4797.72, 1605.0, 1e+16, 1e-05, nan, inf, -0.0.
// JavaScript finds the same shortest digits, but writes them in another way (1605, 1e-5).
export function pyFloatRepr(x) {
  if (Number.isNaN(x)) return 'nan'
  if (x === Infinity) return 'inf'
  if (x === -Infinity) return '-inf'
  if (x === 0) return Object.is(x, -0) ? '-0.0' : '0.0'
  // The shortest digits that give x back, and where the decimal point goes (decpt: after so many digits)
  const text = String(Math.abs(x)) // e.g. '4797.72', '1e+21', '1.5e-7', '0.0001'
  let mantissa = text
  let exponent = 0
  const ePosition = text.indexOf('e')
  if (ePosition >= 0) {
    mantissa = text.slice(0, ePosition)
    exponent = Number(text.slice(ePosition + 1))
  }
  const parts = mantissa.split('.')
  let digits = parts[0] + (parts.length > 1 ? parts[1] : '')
  let decpt = parts[0].length + exponent
  while (digits.length > 1 && digits[0] === '0') {
    digits = digits.slice(1)
    decpt -= 1
  }
  digits = digits.replace(/0+$/, '')
  if (digits === '') digits = '0'

  let written
  if (decpt <= -4 || decpt > 16) {
    // Python's exponent form: one digit, maybe a fraction, and at least two exponent digits
    const power = decpt - 1
    written = digits[0] + (digits.length > 1 ? '.' + digits.slice(1) : '') +
      'e' + (power < 0 ? '-' : '+') + String(Math.abs(power)).padStart(2, '0')
  } else if (decpt <= 0) {
    written = '0.' + '0'.repeat(-decpt) + digits
  } else if (decpt >= digits.length) {
    written = digits + '0'.repeat(decpt - digits.length) + '.0'
  } else {
    written = digits.slice(0, decpt) + '.' + digits.slice(decpt)
  }
  return (x < 0 ? '-' : '') + written
}

// Python's round(x, digits) for a float: the nearest number with that many decimals, and at
// exactly half way the even one (round(0.25, 1) is 0.2; JavaScript's toFixed would give 0.3)
export function pyRound(x, digits) {
  if (!Number.isFinite(x) || Math.abs(x) >= 1e21) return x
  // x lies exactly half way between two candidates when x * 2 * 10^digits is an odd whole number.
  // A float is a fraction with a power of 2 below, so that happens exactly when x * 2^(digits+1)
  // is an odd whole number m; then x * 2 * 10^digits is m * 5^digits (BigInt: no digit is lost).
  const scaled = x * 2 ** (digits + 1)
  if (Number.isInteger(scaled) && scaled % 2 !== 0) {
    const doubled = BigInt(scaled) * 5n ** BigInt(digits) // x * 2 * 10^digits
    const lower = (doubled - 1n) / 2n // the two candidates, in units of 10^-digits
    const upper = (doubled + 1n) / 2n
    const even = lower % 2n === 0n ? lower : upper
    return Number(even.toString() + 'e-' + digits)
  }
  // Otherwise toFixed gives the correctly rounded decimal text
  return Number(x.toFixed(digits))
}

// Python's sum() of floats (since Python 3.12): Neumaier's compensated sum, which loses
// less precision than adding one by one. The backend's hourly/daily means use it.
export function pySum(values) {
  if (values.length === 0) return 0
  let total = 0 + values[0]
  let compensation = 0
  for (let i = 1; i < values.length; i++) {
    const x = values[i]
    const next = total + x
    if (Math.abs(total) >= Math.abs(x)) compensation += (total - next) + x
    else compensation += (x - next) + total
    total = next
  }
  if (compensation !== 0 && Number.isFinite(compensation)) total += compensation
  return total
}

// Characters Python's repr writes as an escape: control characters and other "not printable" ones
const NOT_PRINTABLE = /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}\p{Zs}]/u

// A text as Python's repr writes it: 'abc', "it's", 'tab\there'
export function pyRepr(text) {
  const quote = text.includes("'") && !text.includes('"') ? '"' : "'"
  let written = quote
  for (const character of text) {
    if (character === '\\') written += '\\\\'
    else if (character === quote) written += '\\' + quote
    else if (character === '\t') written += '\\t'
    else if (character === '\n') written += '\\n'
    else if (character === '\r') written += '\\r'
    else if (character !== ' ' && NOT_PRINTABLE.test(character)) {
      const code = character.codePointAt(0)
      if (code < 0x100) written += '\\x' + code.toString(16).padStart(2, '0')
      else if (code < 0x10000) written += '\\u' + code.toString(16).padStart(4, '0')
      else written += '\\U' + code.toString(16).padStart(8, '0')
    } else written += character
  }
  return written + quote
}

// The prefixes rdflib knows by itself (Graph() binds them): rdflib's error messages use them
// too (source_reading.message_text), next to the prefixes of the document
const RDFLIB_PREFIXES = {
  brick: 'https://brickschema.org/schema/Brick#',
  csvw: 'http://www.w3.org/ns/csvw#',
  dc: 'http://purl.org/dc/elements/1.1/',
  dcat: 'http://www.w3.org/ns/dcat#',
  dcmitype: 'http://purl.org/dc/dcmitype/',
  dcterms: 'http://purl.org/dc/terms/',
  dcam: 'http://purl.org/dc/dcam/',
  doap: 'http://usefulinc.com/ns/doap#',
  foaf: 'http://xmlns.com/foaf/0.1/',
  geo: 'http://www.opengis.net/ont/geosparql#',
  odrl: 'http://www.w3.org/ns/odrl/2/',
  org: 'http://www.w3.org/ns/org#',
  prof: 'http://www.w3.org/ns/dx/prof/',
  prov: 'http://www.w3.org/ns/prov#',
  qb: 'http://purl.org/linked-data/cube#',
  schema: 'https://schema.org/',
  sh: 'http://www.w3.org/ns/shacl#',
  skos: 'http://www.w3.org/2004/02/skos/core#',
  sosa: 'http://www.w3.org/ns/sosa/',
  ssn: 'http://www.w3.org/ns/ssn/',
  time: 'http://www.w3.org/2006/time#',
  vann: 'http://purl.org/vocab/vann/',
  void: 'http://rdfs.org/ns/void#',
  wgs: 'https://www.w3.org/2003/01/geo/wgs84_pos#',
  owl: 'http://www.w3.org/2002/07/owl#',
  rdf: 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
  rdfs: 'http://www.w3.org/2000/01/rdf-schema#',
  xsd: 'http://www.w3.org/2001/XMLSchema#',
  xml: 'http://www.w3.org/XML/1998/namespace',
}

// The prefixes rdflib would use for a document: its own defaults, then the document's @prefix lines
export function rdflibPrefixes(prefixes) {
  return Object.assign({}, RDFLIB_PREFIXES, prefixes ? prefixes : {})
}

// Where rdflib splits an IRI into a namespace and a local name (rdflib.namespace.split_uri):
// the local name is the longest run of name characters at the end, from its first letter, digit or _.
// Gives [namespace, local name], or null when it can't split the IRI.
const NAME_CHARACTER = /[\p{L}\p{Nl}\p{Nd}\p{Mn}\p{Mc}\p{Me}]/u
const ALLOWED_NAME_CHARACTERS = ['\u00B7', '\u0387', '-', '.', '_', '%', '(', ')']
const SPLIT_START = /[\p{Ll}\p{Lu}\p{Lo}\p{Lt}\p{Nl}\p{Nd}_]/u
function splitIri(iri) {
  const characters = Array.from(iri)
  for (let i = characters.length - 1; i >= 0; i--) {
    const character = characters[i]
    if (NAME_CHARACTER.test(character) || ALLOWED_NAME_CHARACTERS.includes(character)) continue
    for (let j = i; j < characters.length; j++) {
      if (SPLIT_START.test(characters[j])) {
        if (j === 0) return null
        return [characters.slice(0, j).join(''), characters.slice(j).join('')]
      }
    }
    return null
  }
  return null
}

// An IRI as rdflib writes it with a namespace manager: prefix:local, or <iri>
function rdflibName(iri, prefixTable) {
  const split = splitIri(iri)
  if (split !== null) {
    for (const prefix of Object.keys(prefixTable)) {
      if (prefixTable[prefix] === split[0]) return prefix + ':' + split[1]
    }
  }
  return '<' + iri + '>'
}

// How an RDF term is written in an error message, like rdflib's term.n3(namespace_manager)
// (source_reading.message_text): xsd:double, <https://...>, "abc"^^xsd:integer, "Value"@en
//   prefixes: the document's prefixes (rdf.prefixes); rdflib's own are added
export function messageText(term, prefixes) {
  if (term === null || term === undefined) return '(not stated)'
  return termTextWith(term, rdflibPrefixes(prefixes))
}

// The same, with exactly these prefixes (prefix -> namespace), e.g. the prefixes of the
// converter's preview graph. An empty table writes every IRI in full: <https://...>
export function termTextWith(term, table) {
  if (term.termType === 'NamedNode') return rdflibName(term.value, table)
  if (term.termType === 'BlankNode') return '_:' + term.value
  if (term.termType === 'Literal') {
    const quoted = '"' + term.value.replaceAll('\\', '\\\\').replaceAll('"', '\\"')
      .replaceAll('\n', '\\n').replaceAll('\r', '\\r') + '"'
    if (term.language) return quoted + '@' + term.language
    if (term.datatype && term.datatype.value !== XSD + 'string') return quoted + '^^' + rdflibName(term.datatype.value, table)
    return quoted
  }
  return String(term.value)
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

// A whole number from the address, like FastAPI reads an int parameter (limit, lines). A wrong
// one gets FastAPI's answer (a 422 with a list of problems), written as text like errorText does.
export function parseWholeNumber(value, name, fallback) {
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
// Numbers (value_reading.read_number / literal_number)
// ---------------------------------------------------------------------------

// A whole number as Python's int() reads a text (spaces around it and _ between digits are fine),
// or null. (Bigger than 2^53 loses precision here; the backend keeps it exact.)
function pyInt(text) {
  if (!/^\s*[+-]?[0-9](?:_?[0-9])*\s*$/.test(text)) return null
  return Number(text.trim().replaceAll('_', ''))
}

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
// The source and its fields (source_reading.read_source)
// ---------------------------------------------------------------------------

// A tss:columnIndex as a number (zero-based), or null when it is not a whole number of 0 or more
// (tss2rdf.column_index: rdflib reads integer datatypes with int())
export function columnIndex(term) {
  if (!term || term.termType !== 'Literal') return null
  if (XSD_TO_DUCKDB[term.datatype.value] !== 'BIGINT') return null
  const number = pyInt(term.value)
  return number !== null && number >= 0 ? number : null
}

// The source's tss:embedded as true or false, or null when it does not have exactly one
// boolean (tss2rdf.embedded_mode)
export function embeddedMode(store, source) {
  const values = all(store, source, TSS_EMBEDDED)
  if (values.length !== 1) return null
  return pythonBoolean(values[0])
}

// A boolean property (tss:header, tss:mutable) as true or false, or null (source_reading.boolean_value)
export function booleanProperty(store, node, property) {
  return pythonBoolean(one(store, node, property))
}

// Python compares texts by their characters' code points (JavaScript's < compares UTF-16 units)
export function compareCodePoints(a, b) {
  const charactersA = Array.from(a)
  const charactersB = Array.from(b)
  const length = Math.min(charactersA.length, charactersB.length)
  for (let i = 0; i < length; i++) {
    const difference = charactersA[i].codePointAt(0) - charactersB[i].codePointAt(0)
    if (difference !== 0) return difference
  }
  return charactersA.length - charactersB.length
}

// The order of the fields (source_reading.field_order): by tss:columnIndex when they have one,
// otherwise the time field first and then the rest by column name
function fieldOrderKey(field) {
  if (field.columnIndex !== null) return [0, field.columnIndex, '']
  if (field.isTime) return [1, 0, '']
  return [2, 0, field.columnName || '']
}

function compareFieldOrder(a, b) {
  const keyA = fieldOrderKey(a)
  const keyB = fieldOrderKey(b)
  if (keyA[0] !== keyB[0]) return keyA[0] - keyB[0]
  if (keyA[1] !== keyB[1]) return keyA[1] - keyB[1]
  return compareCodePoints(keyA[2], keyB[2])
}

// One field of the source (source_reading.read_field)
function readField(store, field, timeField) {
  const column = one(store, field, TSS_COLUMN_NAME)
  return {
    node: field,                                      // the field (a term); field.value is its IRI
    columnName: column !== null ? column.value : null,
    columnIndex: columnIndex(one(store, field, TSS_COLUMN_INDEX)),
    datatype: one(store, field, TSS_DATATYPE),        // a term such as xsd:double, or null
    isTime: timeField !== null && field.equals(timeField),
    label: one(store, field, RDFS_LABEL),
    observedProperty: one(store, field, TSS_OBSERVED_PROPERTY),
    unit: one(store, field, TSS_UNIT),
    describes: one(store, field, TSS_DESCRIBES_FIELD), // the field this one says something about
  }
}

// What the data routes need to know about the tss:Source and its fields (source_reading.read_source).
// dataFile is the tss:dataFile IRI as text: in the browser there is no data folder, so it is the
// "location" of the data file in messages (the backend writes data/x.csv for its own files).
export function readSource(store, source) {
  const timeField = one(store, source, TSS_TIME_FIELD)
  const fields = []
  for (const field of all(store, source, TSS_HAS_FIELD)) {
    fields.push(readField(store, field, timeField))
  }
  fields.sort(compareFieldOrder) // a stable sort, like Python's

  const delimiter = one(store, source, TSS_DELIMITER)
  const encoding = one(store, source, TSS_ENCODING)
  const dataFile = one(store, source, TSS_DATA_FILE)
  return {
    node: source,
    embedded: embeddedMode(store, source),        // true, false, or null (not stated)
    format: one(store, source, TSS_FORMAT),        // a term: compare format.value with TSS_CSV or TSS_PARQUET
    dataFile: dataFile !== null ? dataFile.value : null,
    delimiter: delimiter !== null ? delimiter.value : ',',
    header: booleanProperty(store, source, TSS_HEADER) !== false, // a header line, unless it says false
    encoding: encoding !== null ? encoding.value : 'utf-8',
    timeField: timeField,
    fields: fields,
  }
}

// The field with this IRI (source_reading.find_field)
export function findSourceField(source, fieldIri) {
  for (const field of source.fields) {
    if (field.node.value === fieldIri) return field
  }
  throw new Error(`Unknown field: ${fieldIri}`)
}

// Is the format of the source this IRI (TSS_CSV, TSS_PARQUET)?
export function hasFormat(source, formatIri) {
  return source.format !== null && source.format.termType === 'NamedNode' && source.format.value === formatIri
}

// ---------------------------------------------------------------------------
// Reading a file: its lines, and CSV cells the way Python's csv module reads them
// ---------------------------------------------------------------------------

const CHUNK_SIZE = 1 << 20 // read a file 1 MB at a time

// Go through the lines of a text file (a Blob), the way Python reads a file opened with
// newline="": a line ends at \n, \r\n or \r. visit(text, ending) gets each line without its
// ending, and the ending itself ('' for a last line without one); when it returns false, the
// reading stops. encoding: a label TextDecoder knows ('utf-8', ...). A UTF-8 byte order mark
// stays in the text, as in Python. Gives a Promise that is done when the reading is done.
export async function eachLine(blob, visit, encoding = 'utf-8') {
  let decoder
  try {
    decoder = new TextDecoder(encoding, { fatal: true, ignoreBOM: true })
  } catch {
    throw new Error(`unknown encoding: ${encoding}`)
  }
  let rest = '' // the start of a line whose end is in the next part of the file
  let stopped = false

  // Hand out every complete line in text; keep the incomplete end in rest
  function handOut(text, last) {
    let start = 0
    for (let i = 0; i < text.length; i++) {
      const character = text[i]
      if (character !== '\n' && character !== '\r') continue
      let ending = character
      if (character === '\r') {
        if (i + 1 === text.length && !last) break // a \r at the end: maybe \r\n, wait for more
        if (text[i + 1] === '\n') ending = '\r\n'
      }
      if (visit(text.slice(start, i), ending) === false) {
        stopped = true
        return ''
      }
      i += ending.length - 1
      start = i + 1
    }
    return text.slice(start)
  }

  for (let offset = 0; offset < blob.size && !stopped; offset += CHUNK_SIZE) {
    const bytes = new Uint8Array(await blob.slice(offset, offset + CHUNK_SIZE).arrayBuffer())
    let text
    try {
      text = decoder.decode(bytes, { stream: true })
    } catch {
      throw new Error(`'${encoding}' codec can't decode the file: it is not valid ${encoding} text`)
    }
    rest = handOut(rest + text, false)
  }
  if (stopped) return
  let end
  try {
    end = decoder.decode()
  } catch {
    throw new Error(`'${encoding}' codec can't decode the file: it is not valid ${encoding} text`)
  }
  rest = handOut(rest + end, true)
  if (!stopped && rest !== '') visit(rest, '')
}

// Python's csv module (Modules/_csv.c) with its default dialect: quote character ",
// a quote inside a quoted cell written twice, no escape character, strict off.
// Feed it one line at a time (feed); it gives a record (an array of cells) when a line completes one.
// It is the app's only class: one object (new CsvReader(',')) that keeps where it is in a record
// (this.state, this.field, ...) from one line to the next, like the C code's reader object.
const START_RECORD = 0
const START_FIELD = 1
const IN_FIELD = 2
const IN_QUOTED_FIELD = 3
const QUOTE_IN_QUOTED_FIELD = 4
const EAT_CRNL = 5
const FIELD_LIMIT = 131072 // csv.field_size_limit()

export class CsvReader {
  constructor(delimiter) {
    this.delimiter = delimiter
    this.lineNumber = 0 // how many lines were read (csv's reader.line_num)
    this.fields = []
    this.field = ''
    this.state = START_RECORD
  }

  saveField() {
    this.fields.push(this.field)
    this.field = ''
  }

  addCharacter(character) {
    if (this.field.length >= FIELD_LIMIT) throw new Error(`field larger than field limit (${FIELD_LIMIT})`)
    this.field += character
  }

  // One character; null is the end of a line (EOL in _csv.c)
  process(c) {
    const newline = c === '\n' || c === '\r'
    switch (this.state) {
      case START_RECORD:
        if (c === null) return // an empty line: an empty record
        if (newline) {
          this.state = EAT_CRNL
          return
        }
        this.state = START_FIELD
      // falls through: a normal character starts the first cell
      case START_FIELD:
        if (newline || c === null) {
          this.saveField()
          this.state = c === null ? START_RECORD : EAT_CRNL
        } else if (c === '"') {
          this.state = IN_QUOTED_FIELD
        } else if (c === this.delimiter) {
          this.saveField()
        } else {
          this.addCharacter(c)
          this.state = IN_FIELD
        }
        return
      case IN_FIELD:
        if (newline || c === null) {
          this.saveField()
          this.state = c === null ? START_RECORD : EAT_CRNL
        } else if (c === this.delimiter) {
          this.saveField()
          this.state = START_FIELD
        } else {
          this.addCharacter(c)
        }
        return
      case IN_QUOTED_FIELD:
        if (c === null) return // the cell goes on on the next line
        if (c === '"') this.state = QUOTE_IN_QUOTED_FIELD
        else this.addCharacter(c)
        return
      case QUOTE_IN_QUOTED_FIELD:
        if (c === '"') {
          this.addCharacter(c) // "" inside quotes is one "
          this.state = IN_QUOTED_FIELD
        } else if (c === this.delimiter) {
          this.saveField()
          this.state = START_FIELD
        } else if (newline || c === null) {
          this.saveField()
          this.state = c === null ? START_RECORD : EAT_CRNL
        } else {
          this.addCharacter(c) // strict off: a character after the closing quote is kept
          this.state = IN_FIELD
        }
        return
      case EAT_CRNL:
        if (newline) return
        if (c === null) {
          this.state = START_RECORD
          return
        }
        throw new Error("new-line character seen in unquoted field - do you need to open the file with newline=''?")
    }
  }

  // One line: its text, with its line ending or without it. Gives the record it completes, or
  // null when a quoted cell goes on on the next line.
  feed(text) {
    this.lineNumber += 1
    // A line without quotes that starts a record: the cells are the parts between the delimiters
    if (this.state === START_RECORD && !text.includes('"') && this.delimiter.length === 1 &&
        !/[\r\n]./.test(text)) {
      const content = text.replace(/[\r\n]+$/, '')
      if (content === '') return []
      const cells = content.split(this.delimiter)
      for (const cell of cells) {
        if (cell.length > FIELD_LIMIT) throw new Error(`field larger than field limit (${FIELD_LIMIT})`)
      }
      return cells
    }
    for (const character of text) this.process(character)
    this.process(null)
    if (this.state !== START_RECORD) return null
    const record = this.fields
    this.fields = []
    return record
  }

  // The end of the file: a last record that was not finished (a quoted cell without its end), or null
  finish() {
    if (this.field.length !== 0 || this.state === IN_QUOTED_FIELD) {
      this.saveField()
      const record = this.fields
      this.fields = []
      return record
    }
    return null
  }
}

// Go through the records of a CSV file (a Blob), the way data_reading.py does it: csv.reader
// over the lines of the file without their line endings (files.read_lines). visit(cells) gets
// each record; returning false stops the reading.
export async function eachCsvRecord(blob, delimiter, visit) {
  const reader = new CsvReader(delimiter)
  let stopped = false
  await eachLine(blob, (text) => {
    const record = reader.feed(text)
    if (record !== null && visit(record) === false) {
      stopped = true
      return false
    }
    return true
  })
  if (!stopped) {
    const last = reader.finish()
    if (last !== null) visit(last)
  }
}

// The first line of a CSV file as cells (next(csv.reader(...), [])): the header line
export async function firstCsvRecord(blob, delimiter) {
  let first = []
  await eachCsvRecord(blob, delimiter, (cells) => {
    first = cells
    return false
  })
  return first
}

// ---------------------------------------------------------------------------
// The rows of the data (data_reading.py)
// ---------------------------------------------------------------------------

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
// field IRI -> its Literal. The points were lifted once by App.jsx (rdf.points, see readPoints
// in tss.js); when they are not there, they are lifted here.
export async function pointRowsOf(data, source) {
  let points = data.points
  if (!points) {
    const lifted = await liftPoints(data.store, source.node)
    points = { rows: pointRows(lifted, data.store, source.node), error: null }
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
        throw new Error(`A point in tss:points has a JSON object as its ${pyRepr(name)} value, not a value.`)
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

// The text of a field's value in a row: a cell's text, or a point literal's text; '' when the
// row has no value for the field (value_reading.value_text)
export function valueText(row, fieldIri) {
  const value = row[fieldIri]
  if (value === undefined) return ''
  return typeof value === 'string' ? value : value.value
}

// The column names and the first rows of the Parquet file, each value as text the way
// data_reading.cell_text writes it. DuckDB (duck.js) reads the file: its table holds the
// file's rows with the file's own types. limit: null for every row.
async function parquetCells(data, location, limit) {
  if (!data.getDatabase) throw new Error(`Could not read the Parquet file ${location}: no DuckDB database was given (getDatabase)`)
  const db = await data.getDatabase()
  return db.parquetCells('preview', limit)
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
    let table
    try {
      table = await parquetCells(data, location, null)
    } catch (error) {
      throw new Error(`Could not read the Parquet file ${location}: ${error.message}`, { cause: error })
    }
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
    let table
    try {
      table = await parquetCells(data, location, 0)
    } catch (error) {
      throw new Error(`Could not read the Parquet file ${location}: ${error.message}`, { cause: error })
    }
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
    throw new Error('This source has no data file: its points are embedded in the Turtle file (tss:points).')
  }
  let count = parseWholeNumber(lines, 'lines', 20)
  count = Math.max(1, Math.min(count, 1000))
  const location = source.dataFile
  if (location === null) throw new Error('The source has no tss:dataFile.')
  if (!data.dataFile) return { found: false, note: 'The data file (tss:dataFile) was not found.', text: null }

  if (hasFormat(source, TSS_PARQUET)) {
    let table
    try {
      table = await parquetCells(data, location, count - 1) // count - 1: the header line is a line too
    } catch (error) {
      throw new Error(`Could not preview Parquet file ${location}: ${error.message}`, { cause: error })
    }
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
  const columns = await dataColumns(data, source)
  const rows = []
  if (hasFormat(source, TSS_PARQUET) && source.embedded !== true) {
    // Only the first rows of a Parquet file are asked from DuckDB
    const location = dataLocation(data, source)
    let table
    try {
      table = await parquetCells(data, location, count)
    } catch (error) {
      throw new Error(`Could not read the Parquet file ${location}: ${error.message}`, { cause: error })
    }
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

// A number of seconds as text people can read, e.g. 2 d 3 h 15 min (query_helpers.duration_text)
function durationText(seconds) {
  // Python's int(seconds // 60). For dividing by 60, Math.floor(seconds / 60) gives the same
  // whole number for every float: a division rounded up to a whole number would need a
  // seconds value closer to a multiple of 60 than a float can be.
  let minutes = Math.floor(seconds / 60)
  const days = Math.floor(minutes / (24 * 60))
  const hours = Math.floor(minutes / 60) % 24
  minutes = minutes % 60
  const parts = []
  if (days > 0) parts.push(`${days} d`)
  if (hours > 0) parts.push(`${hours} h`)
  if (minutes > 0 || parts.length === 0) parts.push(`${minutes} min`)
  return parts.join(' ')
}

// Count, mean, median, min and max of the readings, and the first and last time with a value
// (query_helpers.summarize). Each reading is { moment, value }.
function summarize(readings) {
  let count = 0
  let empty = 0
  let total = 0
  let lowest = null
  let highest = null
  const values = [] // every value, for the median
  let firstTime = null
  let lastTime = null
  for (const reading of readings) {
    const value = reading.value
    if (value === null) {
      empty += 1
      continue
    }
    if (typeof value !== 'number') continue
    count += 1
    total += value // one by one, like the Python loop
    values.push(value)
    if (lowest === null || value < lowest.value) lowest = reading
    if (highest === null || value > highest.value) highest = reading
    if (firstTime === null || reading.moment < firstTime) firstTime = reading.moment
    if (lastTime === null || reading.moment > lastTime) lastTime = reading.moment
  }

  const summary = {
    count: count, empty: empty, mean: null, median: null, min: null, max: null, firstTime: null, lastTime: null,
  }
  if (count > 0) {
    summary.mean = pyRound(total / count, 3)
    // The middle value; with an even number of values, the mean of the middle two (statistics.median)
    values.sort((a, b) => a - b)
    const middle = Math.floor(values.length / 2)
    summary.median = values.length % 2 === 1 ? values[middle] : (values[middle - 1] + values[middle]) / 2
    summary.min = { value: lowest.value, time: timeText(lowest.moment) }
    summary.max = { value: highest.value, time: timeText(highest.moment) }
    summary.firstTime = timeText(firstTime)
    summary.lastTime = timeText(lastTime)
  }
  return summary
}

// For a field that holds text: how many readings have a value, how many are empty, and the
// first and last time with a value (query_helpers.text_summary)
function textSummary(readings) {
  const summary = {
    count: 0, empty: 0, mean: null, median: null, min: null, max: null, firstTime: null, lastTime: null,
  }
  let firstTime = null
  let lastTime = null
  for (const reading of readings) {
    if (reading.value === null) {
      summary.empty += 1
      continue
    }
    summary.count += 1
    if (firstTime === null || reading.moment < firstTime) firstTime = reading.moment
    if (lastTime === null || reading.moment > lastTime) lastTime = reading.moment
  }
  if (firstTime !== null) {
    summary.firstTime = timeText(firstTime)
    summary.lastTime = timeText(lastTime)
  }
  return summary
}

// Gaps: where the time between two readings is longer than the step that occurs most often
// (query_helpers.find_gaps). moments: in time order.
function findGaps(moments) {
  if (moments.length < 3) return { step: null, count: 0, missing: null, longest: [] }

  // 1. How often each step occurs, in seconds (a Map keeps the order in which steps first occur)
  const steps = new Map()
  for (let i = 1; i < moments.length; i++) {
    const seconds = (moments[i] - moments[i - 1]) / 1000000
    steps.set(seconds, (steps.get(seconds) || 0) + 1)
  }
  let usual = null
  for (const [seconds, howOften] of steps) {
    if (usual === null || howOften > steps.get(usual)) usual = seconds
  }

  // 2. Every step longer than the usual one is a gap
  const gaps = []
  let missing = 0
  for (let i = 1; i < moments.length; i++) {
    const seconds = (moments[i] - moments[i - 1]) / 1000000
    if (seconds > usual) {
      gaps.push({ seconds: seconds, before: moments[i - 1], after: moments[i] })
      missing += seconds - usual
    }
  }
  // Longest first (and, for the same length, the later one first, like Python's reversed tuple sort)
  gaps.sort((a, b) => b.seconds - a.seconds || b.before - a.before || b.after - a.after)

  const longest = []
  for (const gap of gaps.slice(0, 5)) {
    longest.push({ from: timeText(gap.before), until: timeText(gap.after), length: durationText(gap.seconds) })
  }
  return { step: durationText(usual), count: gaps.length, missing: durationText(missing), longest: longest }
}

// The readings in groups per hour or per day, each group one value: the mean (rounded to 3
// decimals), the min or the max of its values (query_helpers.resample_readings)
function resampleReadings(readings, period, aggregate) {
  const size = period === 'hour' ? 3600 * 1000000 : 24 * 3600 * 1000000
  const groups = new Map() // the start of the hour (or day) -> its values; a Map keeps the order
  for (const reading of readings) {
    const start = reading.moment - (((reading.moment % size) + size) % size)
    if (!groups.has(start)) groups.set(start, [])
    if (reading.value !== null) groups.get(start).push(reading.value)
  }

  const rows = []
  for (const [start, values] of groups) {
    let value
    if (values.length === 0) value = null
    else if (aggregate === 'min') value = values.reduce((low, x) => (x < low ? x : low))
    else if (aggregate === 'max') value = values.reduce((high, x) => (x > high ? x : high))
    else value = pyRound(pySum(values) / values.length, 3)
    rows.push({ time: timeText(start), value: value, count: values.length })
  }
  return rows
}

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
