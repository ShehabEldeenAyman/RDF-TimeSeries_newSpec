// sourceReading.js: what the routes need to know about the endpoint's tss:Source and its fields,
// read from the metadata (an n3 Store): readSource gives the source's settings (format, data file,
// delimiter, ...) and its fields in their order, each with its column, datatype, unit, ...
//
// It is a port of code/webapp/backend/source_reading.py (read_source, read_field, field_order,
// find_field, boolean_value), with column_index and embedded_mode of code/TSS2RDF/tss2rdf.py.
// (source_reading.message_text, how a term is written in an error message, is messageText in
// python.js.) Used by dataReading.js, query.js, duck.js, sqlBox.js and observationsWorker.js.

import {
  all,
  one,
  RDFS_LABEL,
  TSS_COLUMN_INDEX,
  TSS_COLUMN_NAME,
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
  TSS_TIME_FIELD,
  TSS_UNIT,
} from '../tss.js'
import { compareCodePoints, pyInt } from './python.js'
import { pythonBoolean, XSD_TO_DUCKDB } from './values.js'

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
function embeddedMode(store, source) {
  const values = all(store, source, TSS_EMBEDDED)
  if (values.length !== 1) return null
  return pythonBoolean(values[0])
}

// A boolean property (tss:header, tss:mutable) as true or false, or null (source_reading.boolean_value)
export function booleanProperty(store, node, property) {
  return pythonBoolean(one(store, node, property))
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
