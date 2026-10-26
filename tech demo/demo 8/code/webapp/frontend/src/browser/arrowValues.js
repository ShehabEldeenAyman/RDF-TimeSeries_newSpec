// arrowValues.js: turns the values DuckDB-WASM answers with into the values Python's duckdb gives
// the backend, so the SQL box, the data routes and the converter write them the same way.
//
// DuckDB-WASM answers in Apache Arrow: a BIGINT is a JavaScript BigInt, a time a number of
// microseconds, and so on. The backend's answers come from Python, which writes each value its
// own way (sql_box.to_json_value, data_reading.cell_text, tss2rdf.cell_text). These functions
// turn an Arrow value into the same JSON value or text.
//   style 'json':    sql_box.to_json_value (the SQL box's answers)
//   style 'preview': data_reading.cell_text (the data routes' text of a Parquet value)
//   style 'rdf':     tss2rdf.cell_text (the converter's text of a Parquet value)
//
// It ports those three functions (in code/webapp/backend/sql_box.py, data_reading.py and
// code/TSS2RDF/tss2rdf.py) and the way Python writes the values they give: str(), repr(),
// isoformat(), a timedelta, a Decimal. Used by duck.js only: db.run (converterFor, pythonLength)
// and the rows of a Parquet file (arrowCells). Big whole numbers come from DuckDB as BigInt
// values: 5n is the BigInt 5, and BigInts only calculate with other BigInts.

import { pyFloatRepr, pyRepr } from './python.js'
import { isoText, isoTextParts } from './values.js'

// The Arrow type ids (apache-arrow's Type enum)
const ARROW_NULL = 1
const ARROW_INT = 2
const ARROW_FLOAT = 3
const ARROW_BINARY = 4
const ARROW_UTF8 = 5
const ARROW_BOOL = 6
const ARROW_DECIMAL = 7
const ARROW_DATE = 8
const ARROW_TIME = 9
const ARROW_TIMESTAMP = 10
const ARROW_INTERVAL = 11
const ARROW_LIST = 12
const ARROW_STRUCT = 13
const ARROW_FIXED_SIZE_BINARY = 15
const ARROW_FIXED_SIZE_LIST = 16
const ARROW_MAP = 17
const ARROW_LARGE_BINARY = 19
const ARROW_LARGE_UTF8 = 20

// Microseconds per time unit of Arrow (SECOND, MILLISECOND, MICROSECOND, NANOSECOND)
const MICROSECONDS_PER_UNIT = [1000000, 1000, 1, 0.001]

// A number of microseconds of an Arrow time of day (a BigInt or a number) in a unit
function toMicroseconds(raw, unit) {
  return Math.floor(Number(raw) * MICROSECONDS_PER_UNIT[unit])
}

// BigInt division rounded down (BigInt's / rounds toward zero)
function floorDivide(a, b) {
  const quotient = a / b
  return a % b < 0n ? quotient - 1n : quotient
}

// A moment of an Arrow timestamp (a BigInt in a unit) as Python's isoformat writes it, in UTC.
// It stays a BigInt until it is split, so no microsecond is lost (a Number holds microseconds
// exactly only for about 285 years around 1970).
function timestampText(raw, unit) {
  const value = BigInt(raw)
  let micro = value
  if (unit === 0) micro = value * 1000000n
  if (unit === 1) micro = value * 1000n
  if (unit === 3) micro = floorDivide(value, 1000n) // nanoseconds: Python keeps microseconds
  const milliseconds = floorDivide(micro, 1000n)
  return isoTextParts(Number(milliseconds), Number(micro - milliseconds * 1000n))
}

// A time of day in microseconds as Python's time.isoformat writes it: 23:45:00 or 23:45:00.500000
function timeOfDayText(micro) {
  const seconds = Math.floor(micro / 1000000)
  const rest = micro - seconds * 1000000
  const text = String(Math.floor(seconds / 3600)).padStart(2, '0') + ':' +
    String(Math.floor(seconds / 60) % 60).padStart(2, '0') + ':' + String(seconds % 60).padStart(2, '0')
  return rest === 0 ? text : text + '.' + String(rest).padStart(6, '0')
}

// A number of microseconds (a BigInt) as Python writes a timedelta: 0:15:00, 85 days, 0:15:00,
// -1 day, 21:00:00
function timedeltaText(micro) {
  const microPerDay = 86400n * 1000000n
  const days = floorDivide(micro, microPerDay)
  const rest = micro - days * microPerDay
  const seconds = Number(rest / 1000000n)
  const fraction = Number(rest % 1000000n)
  let text = Math.floor(seconds / 3600) + ':' + String(Math.floor(seconds / 60) % 60).padStart(2, '0') + ':' +
    String(seconds % 60).padStart(2, '0')
  if (days !== 0n) text = `${days} day${days !== 1n && days !== -1n ? 's' : ''}, ` + text
  if (fraction !== 0) text += '.' + String(fraction).padStart(6, '0')
  return text
}

// A 128-bit decimal from its four 32-bit words (little-endian, two's complement) as a BigInt
function decimalBigInt(words, index) {
  let value = 0n
  for (let k = 3; k >= 0; k--) value = (value << 32n) | BigInt(words[index * 4 + k])
  if (words[index * 4 + 3] & 0x80000000) value -= 1n << 128n
  return value
}

// A decimal (its digits as a BigInt, and its scale) as Python writes a Decimal: str() ('1.50',
// '1E-7') or, with fixed true, in fixed notation like f"{value:f}" ('0.0000001')
function decimalText(unscaled, scale, fixed) {
  const negative = unscaled < 0n
  let digits = (negative ? -unscaled : unscaled).toString()
  const sign = negative ? '-' : ''
  if (scale <= 0) return sign + digits + '0'.repeat(-scale)
  const adjusted = digits.length - 1 - scale
  if (!fixed && adjusted < -6) {
    const mantissa = digits.length > 1 ? digits[0] + '.' + digits.slice(1) : digits
    return sign + mantissa + 'E' + adjusted
  }
  if (digits.length <= scale) digits = '0'.repeat(scale - digits.length + 1) + digits
  return sign + digits.slice(0, digits.length - scale) + '.' + digits.slice(digits.length - scale)
}

// Bytes as Python writes them: b'ab\x00'
function bytesRepr(bytes) {
  let text = ''
  for (const byte of bytes) {
    if (byte === 0x5c) text += '\\\\'
    else if (byte === 0x27) text += "\\'"
    else if (byte === 0x09) text += '\\t'
    else if (byte === 0x0a) text += '\\n'
    else if (byte === 0x0d) text += '\\r'
    else if (byte < 0x20 || byte >= 0x7f) text += '\\x' + byte.toString(16).padStart(2, '0')
    else text += String.fromCharCode(byte)
  }
  return "b'" + text + "'"
}

// Python's repr of a value inside a list or a dict (str([1, 'x', None]) is "[1, 'x', None]")
function nestedRepr(type, value) {
  if (value === null || value === undefined) return 'None'
  switch (type.typeId) {
    case ARROW_BOOL:
      return value ? 'True' : 'False'
    case ARROW_INT:
      return String(value)
    case ARROW_FLOAT:
      return pyFloatRepr(value)
    case ARROW_UTF8:
    case ARROW_LARGE_UTF8:
      return pyRepr(value)
    case ARROW_LIST:
    case ARROW_FIXED_SIZE_LIST: {
      const items = []
      for (const item of value) items.push(nestedRepr(type.children[0].type, item))
      return '[' + items.join(', ') + ']'
    }
    case ARROW_STRUCT: {
      const items = []
      for (const child of type.children) items.push(pyRepr(child.name) + ': ' + nestedRepr(child.type, value[child.name]))
      return '{' + items.join(', ') + '}'
    }
    case ARROW_MAP: {
      const items = []
      const entry = type.children[0].type
      for (const [key, item] of value) items.push(nestedRepr(entry.children[0].type, key) + ': ' + nestedRepr(entry.children[1].type, item))
      return '{' + items.join(', ') + '}'
    }
    default:
      return String(value)
  }
}

// The function that reads one column's values: convert(vector, row) for an Arrow type and a style.
// It also says whether Python would hold the value as a float (for the 5 MB count of the answer).
export function converterFor(type, style) {
  const json = style === 'json'
  const rdf = style === 'rdf'
  switch (type.typeId) {
    case ARROW_NULL:
      return { convert: () => null, float: false }
    case ARROW_BOOL:
      return {
        convert: (vector, i) => {
          const value = vector.get(i)
          if (json) return value
          if (rdf) return value ? 'true' : 'false'
          return value ? 'True' : 'False'
        },
        float: false,
      }
    case ARROW_INT:
      return {
        // A 64-bit whole number is a BigInt: as JSON it becomes a number, like the browser reads the backend's JSON
        convert: (vector, i) => (json ? Number(vector.get(i)) : String(vector.get(i))),
        float: false,
      }
    case ARROW_FLOAT:
      return {
        convert: (vector, i) => {
          const value = vector.get(i)
          if (json) return Number.isFinite(value) ? value : pyFloatRepr(value)
          if (rdf && Number.isNaN(value)) return 'NaN'
          if (rdf && !Number.isFinite(value)) return value > 0 ? 'INF' : '-INF'
          return pyFloatRepr(value)
        },
        float: true,
      }
    case ARROW_UTF8:
    case ARROW_LARGE_UTF8:
      return { convert: (vector, i) => vector.get(i), float: false }
    case ARROW_DECIMAL:
      return {
        convert: (vector, i) => {
          const unscaled = decimalBigInt(vector.data[0].values, i)
          if (json) return Number(decimalText(unscaled, type.scale, true)) // float(Decimal)
          return decimalText(unscaled, type.scale, rdf)
        },
        float: true,
      }
    case ARROW_DATE:
      return {
        convert: (vector, i) => {
          // Date32: days since 1970 (the raw value); Date64: milliseconds
          const raw = vector.data[0].values[i]
          const milliseconds = type.unit === 0 ? Number(raw) * 86400000 : Number(vector.get(i))
          return isoText(milliseconds * 1000).slice(0, -9) // 2021-03-03T00:00:00 -> 2021-03-03
        },
        float: false,
      }
    case ARROW_TIME:
      return { convert: (vector, i) => timeOfDayText(toMicroseconds(vector.data[0].values[i], type.unit)), float: false }
    case ARROW_TIMESTAMP:
      return {
        convert: (vector, i) => {
          // The raw value (a BigInt); with a timezone it is a moment, written in UTC with Z
          const text = timestampText(vector.data[0].values[i], type.unit)
          return type.timezone ? text + 'Z' : text
        },
        float: false,
      }
    case ARROW_INTERVAL:
      return {
        convert: (vector, i) => {
          // DuckDB sends an interval as months, days and nanoseconds (16 bytes); this version of
          // Arrow does not read that layout itself, so the four 32-bit words are read here.
          // Python makes a timedelta of it, with 30 days per month.
          const words = vector.data[0].values
          const months = words[i * 4]
          const days = words[i * 4 + 1]
          const nanos = (BigInt(words[i * 4 + 3]) << 32n) | BigInt(words[i * 4 + 2] >>> 0)
          return timedeltaText(BigInt(months * 30 + days) * 86400000000n + nanos / 1000n)
        },
        float: false,
      }
    case ARROW_BINARY:
    case ARROW_LARGE_BINARY:
    case ARROW_FIXED_SIZE_BINARY:
      return {
        convert: (vector, i) => {
          const bytes = vector.get(i)
          if (rdf) return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('').toUpperCase()
          return bytesRepr(bytes)
        },
        float: false,
      }
    case ARROW_LIST:
    case ARROW_FIXED_SIZE_LIST:
    case ARROW_STRUCT:
    case ARROW_MAP:
      return { convert: (vector, i) => nestedRepr(type, vector.get(i)), float: false }
    default:
      // Anything else (an ENUM, a UUID, ...): its text
      return { convert: (vector, i) => String(vector.get(i)), float: false }
  }
}

// How long str(value) is in Python, for the 5 MB limit of an answer (sql_box.run_query)
export function pythonLength(value, float) {
  if (value === null) return 4 // None
  if (value === true) return 4 // True
  if (value === false) return 5 // False
  if (typeof value === 'number') return float ? pyFloatRepr(value).length : String(value).length
  // A text: Python counts characters (code points), JavaScript UTF-16 units
  const pairs = value.match(/[\uD800-\uDBFF][\uDC00-\uDFFF]/g)
  return value.length - (pairs ? pairs.length : 0)
}

// Every row of an Arrow table (or batch) as an array of converted values
function convertRows(batch, converters) {
  const rows = []
  const columns = []
  for (let j = 0; j < converters.length; j++) columns.push(batch.getChildAt(j))
  for (let i = 0; i < batch.numRows; i++) {
    const values = []
    for (let j = 0; j < converters.length; j++) {
      values.push(columns[j].isValid(i) ? converters[j].convert(columns[j], i) : null)
    }
    rows.push(values)
  }
  return rows
}

// The rows of an Arrow table as text cells ('' for an empty value), and its column names
export function arrowCells(answer, style) {
  const names = answer.schema.fields.map((field) => field.name)
  const converters = answer.schema.fields.map((field) => converterFor(field.type, style))
  const rows = []
  for (const batch of answer.batches) {
    for (const row of convertRows(batch, converters)) {
      rows.push(row.map((value) => (value === null ? '' : value))) // Python writes None as ''
    }
  }
  return { names: names, rows: rows }
}
