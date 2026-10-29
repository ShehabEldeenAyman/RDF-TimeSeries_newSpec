// tss.js: small helpers to read TSS metadata (https://w3id.org/tss#) from an N3 Store.
//
// The store holds the parsed metadata document (see rdf.js). These helpers ask it
// questions and give back RDF/JS terms, not strings:
//   a NamedNode (an IRI)   term.termType === 'NamedNode', term.value is the full IRI
//   a BlankNode            term.termType === 'BlankNode'
//   a Literal (a value)    term.termType === 'Literal', term.value is its text,
//                          term.datatype a NamedNode (e.g. xsd:double), term.language e.g. 'en'
// Only the helpers whose name says Text give strings (oneText, termText); the helpers whose
// name says Value read a typed literal (numberValue, booleanValue, dateTimeValue, timeValue).
// Compare IRIs by their full text: format.value === TSS_CSV, never with 'tss:CSV'
// (a document may use any prefix it likes, or none at all).
//
// Usage:
//   import { loadRdf } from './rdf.js'
//   import { findSource, readFields, one, oneText, termText, TSS_FORMAT, TSS_CSV,
//            TSS_COLUMN_NAME, liftPoints, pointRows, readPoints } from './tss.js'
//
//   loadRdf('/files/metadata%20endpoint/waterinfo_289423042_tss2.ttl').then((rdf) => {
//     const source = findSource(rdf.store)                  // NamedNode ex:grootdok-source, or null
//     const format = one(rdf.store, source, TSS_FORMAT)     // NamedNode tss:CSV, or null
//     if (format && format.value === TSS_CSV) console.log('a CSV file')
//     for (const field of readFields(rdf.store, source)) {
//       console.log(oneText(rdf.store, field, TSS_COLUMN_NAME))   // 'ts_id', 'Timestamp', ...
//     }
//     console.log(termText(format, rdf.prefixes))           // 'tss:CSV' (for display only)
//   })
//
//   // Embedded points (tss:embedded true): turn the tss:points JSON into RDF, then into rows
//   liftPoints(rdf.store, source).then((points) => {
//     const rows = pointRows(points, rdf.store, source)     // ordered by time
//     // rows[0] = { point: BlankNode, time: Literal, values: { 'https://example.org/ex#...-field': Literal } }
//   })
//
//   // Both at once, as App.jsx does for every tab: null (not embedded), { store, rows, error: null },
//   // or { store: null, rows: null, error: 'why the points can't be read' }
//   readPoints(rdf.store, source).then((points) => console.log(points))

import { DataFactory } from 'n3'
import { parseRdf } from './rdf.js'

const { namedNode } = DataFactory

// --- Namespaces: the common start of the IRIs of a vocabulary -----------------

export const TSS = 'https://w3id.org/tss#'
export const RDF = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'
export const RDFS = 'http://www.w3.org/2000/01/rdf-schema#'
export const XSD = 'http://www.w3.org/2001/XMLSchema#'
export const DCAT = 'http://www.w3.org/ns/dcat#'
export const DCTERMS = 'http://purl.org/dc/terms/'
export const SH = 'http://www.w3.org/ns/shacl#'
export const SOSA = 'http://www.w3.org/ns/sosa/'
export const QUDT = 'http://qudt.org/schema/qudt/'
export const PROV = 'http://www.w3.org/ns/prov#'

// When a key in tss:points is not the tss:columnName of any field, the JSON-LD parser
// gives it a predicate under this namespace (instead of dropping it without a word).
// The tables ignore these triples; SHACL reports them as a warning.
export const UNMAPPED = 'urn:tss:unmapped:'

// Prefixes that shortName may use when the document itself does not declare them
export const KNOWN_PREFIXES = {
  tss: TSS,
  rdf: RDF,
  rdfs: RDFS,
  xsd: XSD,
  dcat: DCAT,
  dcterms: DCTERMS,
  sh: SH,
  sosa: SOSA,
  qudt: QUDT,
  prov: PROV,
}

// --- The terms the app uses (full IRIs, as strings) ---------------------------

export const RDF_TYPE = RDF + 'type'               // rdf:type ("a" in Turtle)
export const RDF_JSON = RDF + 'JSON'               // rdf:JSON, the datatype of tss:points
export const RDF_LANG_STRING = RDF + 'langString'  // rdf:langString, text with a language tag

export const RDFS_LABEL = RDFS + 'label'
export const RDFS_COMMENT = RDFS + 'comment'

export const XSD_STRING = XSD + 'string'
export const XSD_BOOLEAN = XSD + 'boolean'
export const XSD_DATE_TIME = XSD + 'dateTime'
export const XSD_DATE_TIME_STAMP = XSD + 'dateTimeStamp' // an xsd:dateTime that must have a timezone

// The source and its fields
export const TSS_SOURCE = TSS + 'Source'           // tss:Source (a class)
export const TSS_FIELD = TSS + 'Field'             // tss:Field (a class)
export const TSS_HAS_FIELD = TSS + 'hasField'
export const TSS_TIME_FIELD = TSS + 'timeField'
export const TSS_KEY = TSS + 'key'
export const TSS_MUTABLE = TSS + 'mutable'
export const TSS_PRODUCED_BY = TSS + 'producedBy'
export const TSS_FROM = TSS + 'from'
export const TSS_UNTIL = TSS + 'until'
export const TSS_COMMITTED_UNTIL = TSS + 'committedUntil'  // the last time the source and its data were checked up to date
// Where the readings are: in a data file, or embedded in the metadata
export const TSS_EMBEDDED = TSS + 'embedded'
export const TSS_POINTS = TSS + 'points'
export const TSS_DATA_FILE = TSS + 'dataFile'
export const TSS_FORMAT = TSS + 'format'
export const TSS_CSV = TSS + 'CSV'                 // tss:CSV (a value of tss:format)
export const TSS_PARQUET = TSS + 'Parquet'         // tss:Parquet (a value of tss:format)
export const TSS_DELIMITER = TSS + 'delimiter'
export const TSS_HEADER = TSS + 'header'
export const TSS_ENCODING = TSS + 'encoding'
// What a field says about its column
export const TSS_COLUMN_NAME = TSS + 'columnName'
export const TSS_COLUMN_INDEX = TSS + 'columnIndex'
export const TSS_DATATYPE = TSS + 'datatype'
export const TSS_LANGUAGE = TSS + 'language'
export const TSS_REQUIRED = TSS + 'required'
export const TSS_MINIMUM = TSS + 'minimum'
export const TSS_MAXIMUM = TSS + 'maximum'
export const TSS_OBSERVED_PROPERTY = TSS + 'observedProperty'
export const TSS_UNIT = TSS + 'unit'
export const TSS_SENSOR = TSS + 'sensor'
export const TSS_FEATURE_OF_INTEREST = TSS + 'featureOfInterest'
export const TSS_DESCRIBES_FIELD = TSS + 'describesField'
// When the source's metadata was last edited (stated on the tss:Source; older documents
// stated it on the document itself: <> in Turtle)
export const TSS_MODIFIED_AT = TSS + 'modifiedAt'
// A field's stored summary: a blank node with numbers computed from the data when the metadata
// was made (they are not refreshed by themselves). Every attribute is optional.
export const TSS_SUMMARY = TSS + 'summary'
export const TSS_MISSING_VALUE_POLICY = TSS + 'missingValuePolicy'  // e.g. tss:ExcludeEmptyValues
export const TSS_COUNT = TSS + 'count'                 // how many values
export const TSS_MISSING = TSS + 'missing'             // how many empty values
export const TSS_FIRST_TIME = TSS + 'firstTime'        // the first and last time with a value
export const TSS_LAST_TIME = TSS + 'lastTime'
export const TSS_OBSERVED_MINIMUM = TSS + 'observedMinimum'  // the numbers: only for numeric fields
export const TSS_OBSERVED_MAXIMUM = TSS + 'observedMaximum'
export const TSS_MEAN = TSS + 'mean'
export const TSS_MEDIAN = TSS + 'median'
export const PROV_GENERATED_AT_TIME = PROV + 'generatedAtTime'  // when the summary was computed

// The attributes of a stored summary, in the order the app shows them:
// a key (for the code), the property, and a name for people
export const SUMMARY_ATTRIBUTES = [
  { key: 'from', iri: TSS_FROM, name: 'from' },
  { key: 'until', iri: TSS_UNTIL, name: 'until' },
  { key: 'policy', iri: TSS_MISSING_VALUE_POLICY, name: 'missing-value policy' },
  { key: 'count', iri: TSS_COUNT, name: 'values' },
  { key: 'missing', iri: TSS_MISSING, name: 'empty values' },
  { key: 'firstTime', iri: TSS_FIRST_TIME, name: 'first time' },
  { key: 'lastTime', iri: TSS_LAST_TIME, name: 'last time' },
  { key: 'minimum', iri: TSS_OBSERVED_MINIMUM, name: 'observed minimum' },
  { key: 'maximum', iri: TSS_OBSERVED_MAXIMUM, name: 'observed maximum' },
  { key: 'mean', iri: TSS_MEAN, name: 'mean' },
  { key: 'median', iri: TSS_MEDIAN, name: 'median' },
  { key: 'generatedAt', iri: PROV_GENERATED_AT_TIME, name: 'generated at' },
]

// The catalogue of endpoints (GET /api/catalog)
export const DCAT_DATASET_CLASS = DCAT + 'Dataset'            // dcat:Dataset (a class)
export const DCAT_DISTRIBUTION = DCAT + 'distribution'        // dcat:distribution: dataset -> distribution
export const DCAT_DOWNLOAD_URL = DCAT + 'downloadURL'
export const DCTERMS_IDENTIFIER = DCTERMS + 'identifier'

// A SHACL validation report
export const SH_VALIDATION_REPORT = SH + 'ValidationReport'   // sh:ValidationReport (a class)
export const SH_CONFORMS = SH + 'conforms'
export const SH_RESULT = SH + 'result'
export const SH_FOCUS_NODE = SH + 'focusNode'
export const SH_RESULT_PATH = SH + 'resultPath'
export const SH_VALUE = SH + 'value'
export const SH_RESULT_SEVERITY = SH + 'resultSeverity'
export const SH_RESULT_MESSAGE = SH + 'resultMessage'
export const SH_SOURCE_SHAPE = SH + 'sourceShape'
export const SH_VIOLATION = SH + 'Violation'
export const SH_WARNING = SH + 'Warning'
export const SH_INFO = SH + 'Info'
export const SH_NAME = SH + 'name'                  // the name of a shape, e.g. "S6 points"
export const DCTERMS_SOURCE = DCTERMS + 'source'    // on the report: the shapes file the backend used

// --- Asking the store ---------------------------------------------------------
// subject, predicate and object may be a full IRI (a string) or an RDF/JS term.

// A full IRI (string) becomes a NamedNode; a term stays as it is
function toTerm(iriOrTerm) {
  if (iriOrTerm === null || iriOrTerm === undefined) return null
  if (typeof iriOrTerm === 'string') return namedNode(iriOrTerm)
  return iriOrTerm
}

// All objects of (subject, predicate), as an array of terms (empty when there are none)
export function all(store, subject, predicate) {
  return store.getObjects(toTerm(subject), toTerm(predicate), null)
}

// The first object of (subject, predicate), or null. When there are several, RDF
// does not say which one is "first", so use this only for properties with one value.
export function one(store, subject, predicate) {
  const objects = all(store, subject, predicate)
  return objects.length > 0 ? objects[0] : null
}

// The text of the first object (e.g. a label or a column name), or '' when there is none
export function oneText(store, subject, predicate) {
  const object = one(store, subject, predicate)
  return object ? object.value : ''
}

// Does the store hold this triple? Leave out (null) a part to mean "anything",
// e.g. has(store, source, TSS_POINTS, null)
export function has(store, subject, predicate, object) {
  return store.countQuads(toTerm(subject), toTerm(predicate), toTerm(object), null) > 0
}

// --- Typed values -------------------------------------------------------------
// A literal's text only means something together with its datatype: "10" is the number
// ten as xsd:integer, but just two characters as xsd:string. So these helpers look
// at the datatype first, and give null when the literal is not of the right kind.

const NUMBER_DATATYPES = [
  'integer', 'decimal', 'double', 'float', 'int', 'long', 'short', 'byte',
  'nonNegativeInteger', 'positiveInteger', 'nonPositiveInteger', 'negativeInteger',
  'unsignedLong', 'unsignedInt', 'unsignedShort', 'unsignedByte',
].map((name) => XSD + name)

// The form of an xsd:dateTime text: a date, "T", a time, maybe a fraction of a second, and
// maybe a timezone ("Z", "+02:00" or "-05:00"), e.g. 2021-03-03T23:45:00Z. The parts:
//   [1] the date and time, '2021-03-03T23:45:00'   [2] the fraction, '.5' (or undefined)
//   [3] the timezone, 'Z' or '+02:00' (undefined when the text has none)
// The same form as LEXICAL_FORMS[XSD.dateTime] in code/TSS2RDF/tss2rdf.py (and SHACL's P4).
const DATE_TIME_FORM = /^(-?[0-9]{4,}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})?$/

// A numeric literal (xsd:integer, xsd:double, ...) -> a JavaScript number, else null.
// (The xsd:double value "NaN", not-a-number, also gives null: there is no number to show.)
export function numberValue(term) {
  if (!term || term.termType !== 'Literal') return null
  if (!NUMBER_DATATYPES.includes(term.datatype.value)) return null
  const text = term.value.trim()
  // xsd:double writes infinity as INF; JavaScript's Number() does not know that word
  if (text === 'INF' || text === '+INF') return Infinity
  if (text === '-INF') return -Infinity
  const number = Number(text)
  // Number('') is 0 and Number('abc') is NaN: both mean the text is not a number
  if (text === '' || Number.isNaN(number)) return null
  return number
}

// Is this datatype (e.g. the tss:datatype of a field) one of the numeric XSD datatypes?
// Only a numeric field has value ranges and mean/min/max (the backend uses the same list:
// XSD_TO_DUCKDB in sql_box.py). null (no datatype) -> false.
export function isNumericDatatype(datatype) {
  return datatype !== null && NUMBER_DATATYPES.includes(datatype.value)
}

// An xsd:boolean literal -> true or false, else null. XSD allows "true", "false", "1", "0".
export function booleanValue(term) {
  if (!term || term.termType !== 'Literal') return null
  if (term.datatype.value !== XSD_BOOLEAN) return null
  const text = term.value.trim()
  if (text === 'true' || text === '1') return true
  if (text === 'false' || text === '0') return false
  return null
}

// An xsd:dateTime literal -> a JavaScript Date, else null. Every tab reads date-times with
// this one function, so they all read them the same way, and the same way as the backend:
//   "2021-03-03T23:45:00Z"      -> 23:45 UTC
//   "2021-03-03T23:45:00+02:00" -> 21:45 UTC
//   "2021-03-03T23:45:00"       -> 23:45 UTC: a time without a timezone is taken as UTC
//                                  (new Date alone would take it as the browser's local time)
// An xsd:dateTimeStamp is read the same way, but it must have a timezone (as in the backend,
// the converter and SHACL): "2021-03-03T23:45:00"^^xsd:dateTimeStamp -> null.
// null for any other datatype (also xsd:date: a day is not a moment), for a text that is not
// in the xsd:dateTime form (e.g. "2021-03-03", a date alone), and for a date-time that does
// not exist (e.g. 30 February).
export function dateTimeValue(term) {
  if (!term || term.termType !== 'Literal') return null
  const stamp = term.datatype.value === XSD_DATE_TIME_STAMP
  if (term.datatype.value !== XSD_DATE_TIME && !stamp) return null
  const parts = DATE_TIME_FORM.exec(term.value)
  if (parts === null) return null

  // Does this date and time exist? The browser quietly turns 30 February into 2 March (and
  // 24:00 into 00:00 of the next day). So we read the date and time alone, as UTC, and
  // check that writing it back (toISOString, always UTC) gives the same text.
  const check = new Date(parts[1] + 'Z')
  if (Number.isNaN(check.getTime()) || check.toISOString().slice(0, 19) !== parts[1]) return null

  // Now the real moment, with its timezone; without one, we add "Z" (UTC) ourselves
  const hasTimezone = parts[3] !== undefined
  if (stamp && !hasTimezone) return null
  const date = new Date(hasTimezone ? term.value : term.value + 'Z')
  return Number.isNaN(date.getTime()) ? null : date
}

// The same moment as a number, milliseconds since 1970 (to compare and sort times), or null.
// "2021-03-03T23:15:00Z" and "2021-03-03T23:15:00+00:00" give the same number.
export function timeValue(term) {
  const date = dateTimeValue(term)
  return date === null ? null : date.getTime()
}

// --- The source and its fields ------------------------------------------------

// The one tss:Source in the store, or null when there is none or more than one
export function findSource(store) {
  const sources = store.getSubjects(namedNode(RDF_TYPE), namedNode(TSS_SOURCE), null)
  return sources.length === 1 ? sources[0] : null
}

// The fields of a source (its tss:hasField objects), in a fixed order: by
// tss:columnIndex when they have one, then by tss:columnName. (The objects of a
// property have no order in RDF, so without this the order would be up to chance.)
export function readFields(store, source) {
  const fields = all(store, source, TSS_HAS_FIELD)
  fields.sort((a, b) => compareFields(store, a, b))
  return fields
}

function compareFields(store, a, b) {
  const indexA = numberValue(one(store, a, TSS_COLUMN_INDEX))
  const indexB = numberValue(one(store, b, TSS_COLUMN_INDEX))
  // A field with a column index goes before a field without one
  if (indexA !== null && indexB === null) return -1
  if (indexA === null && indexB !== null) return 1
  if (indexA !== null && indexB !== null && indexA !== indexB) return indexA - indexB
  // The same index, or none: by column name, and by IRI when even that is the same
  const byName = oneText(store, a, TSS_COLUMN_NAME).localeCompare(oneText(store, b, TSS_COLUMN_NAME), 'en')
  if (byName !== 0) return byName
  return a.value.localeCompare(b.value, 'en')
}

// Is the data embedded in the metadata (tss:embedded true)? true, false, or null when
// tss:embedded is missing or not an xsd:boolean
export function isEmbedded(store, source) {
  return booleanValue(one(store, source, TSS_EMBEDDED))
}

// May the source's data change (tss:mutable true)? true, false, or null when tss:mutable is
// missing or not an xsd:boolean. Only a source that says true accepts new points ("Add data" tab).
export function isMutable(store, source) {
  return booleanValue(one(store, source, TSS_MUTABLE))
}

// A field's stored summary (its tss:summary node), or null when the field has none:
//   { node: the blank node, values: { count: Literal "94853"^^xsd:integer, mean: ..., ... } }
// An attribute the summary does not state is null. (Keys: see SUMMARY_ATTRIBUTES.)
export function readSummary(store, field) {
  const node = one(store, field, TSS_SUMMARY)
  if (node === null) return null
  const values = {}
  for (const attribute of SUMMARY_ATTRIBUTES) {
    values[attribute.key] = one(store, node, attribute.iri)
  }
  return { node: node, values: values }
}

// When the source's metadata was last edited: the tss:modifiedAt literal of the tss:Source,
// or null when the source does not say.
export function sourceModifiedAt(store, source) {
  return one(store, source, TSS_MODIFIED_AT)
}

// The same, the older way: the document says it about itself (<> tss:modifiedAt ... in Turtle).
// The parser reads <> as the document's own IRI: the base IRI it was parsed with (endpoint.iri,
// see App.jsx). The Overview falls back to this for documents written before the property
// moved to the tss:Source.
export function documentModifiedAt(store, documentIri) {
  return one(store, documentIri, TSS_MODIFIED_AT)
}

// --- Showing terms ------------------------------------------------------------

// An IRI as a short name for DISPLAY, e.g. 'tss:CSV', using the document's prefixes
// (and KNOWN_PREFIXES). Without a fitting prefix: '<https://...>'. Never compare these
// texts in code: compare term.value with the full IRI instead.
export function shortName(term, prefixes) {
  if (!term) return ''
  if (typeof term !== 'string' && term.termType !== 'NamedNode') return termText(term, prefixes)
  const iri = typeof term === 'string' ? term : term.value

  const table = Object.assign({}, KNOWN_PREFIXES, prefixes)
  let bestPrefix = null
  let bestNamespace = ''
  for (const prefix of Object.keys(table)) {
    const namespace = table[prefix]
    // The longest namespace that fits wins: with ex: <https://example.org/> and
    // exa: <https://example.org/a/>, https://example.org/a/b becomes exa:b.
    if (iri.startsWith(namespace) && namespace.length > bestNamespace.length && isLocalName(iri.slice(namespace.length))) {
      bestPrefix = prefix
      bestNamespace = namespace
    }
  }
  if (bestPrefix === null) return '<' + iri + '>'
  return bestPrefix + ':' + iri.slice(bestNamespace.length)
}

// Can this text stand after "prefix:" in Turtle? Letters, digits and _ - . in the
// middle, but not "-" or "." first and not "." last. (Turtle allows a few more
// characters; for display this careful subset is enough.)
function isLocalName(text) {
  return /^([A-Za-z0-9_]([A-Za-z0-9_.-]*[A-Za-z0-9_-])?)?$/.test(text)
}

// Any term as display text, written as in Turtle:
//   NamedNode  tss:CSV   or  <http://localhost:5174/files/data/waterinfo_289423042.csv>
//   BlankNode  _:b0
//   Literal    "Timestamp"@en   "0"^^xsd:integer   ","   (no ^^ for plain xsd:string)
//              say "hi" (two lines) -> "say \"hi\"\nnext": escaped, see quoteText
export function termText(term, prefixes) {
  if (!term) return ''
  if (term.termType === 'NamedNode') return shortName(term, prefixes)
  if (term.termType === 'BlankNode') return '_:' + term.value
  if (term.termType === 'Literal') {
    const quoted = quoteText(term.value)
    if (term.language) return quoted + '@' + term.language
    if (term.datatype && term.datatype.value !== XSD_STRING) return quoted + '^^' + shortName(term.datatype, prefixes)
    return quoted
  }
  return term.value
}

// Inside "..." Turtle writes these characters as escapes: a backslash or a double quote as it
// is would end the string or start an escape, and a line break would break the line (Turtle's
// STRING_LITERAL_QUOTE). A tab is allowed, but as \t it can be seen.
const TURTLE_ESCAPES = {
  '\\': '\\\\', // a backslash        -> \\
  '"': '\\"',   // a double quote     -> \"
  '\n': '\\n',  // a new line         -> \n
  '\r': '\\r',  // a carriage return  -> \r
  '\t': '\\t',  // a tab              -> \t
}

// A text between double quotes, as Turtle writes it: say "hi" -> "say \"hi\""
function quoteText(text) {
  let quoted = '"'
  for (const character of text) {
    // Is this character one of the keys of TURTLE_ESCAPES? Then write its escape instead.
    if (Object.hasOwn(TURTLE_ESCAPES, character)) quoted = quoted + TURTLE_ESCAPES[character]
    else quoted = quoted + character
  }
  return quoted + '"'
}

// --- The catalogue of endpoints -----------------------------------------------

// The bundled endpoints in the catalogue (the store of GET /api/catalog), as plain objects
// for the drop down: [{ id, iri, downloadUrl, label, comment }, ...], sorted by id
//   id          its dcterms:identifier, e.g. 'waterinfo_289423042_tss2'
//   iri         the dataset IRI: the document's own IRI (its base IRI when parsing)
//   downloadUrl where the browser fetches the Turtle (dcat:downloadURL)
//   label       its rdfs:label text, or null when the document could not be read
//   comment     why the document could not be read (rdfs:comment), or null
// (The user's own endpoints are not in the catalogue: the browser keeps those itself, see App.jsx.)
export function readCatalog(store) {
  const endpoints = []
  for (const dataset of store.getSubjects(namedNode(RDF_TYPE), namedNode(DCAT_DATASET_CLASS), null)) {
    endpoints.push(readCatalogEntry(store, dataset))
  }
  endpoints.sort((a, b) => a.id.localeCompare(b.id, 'en'))
  return endpoints
}

function readCatalogEntry(store, dataset) {
  const distribution = one(store, dataset, DCAT_DISTRIBUTION)
  const downloadUrl = distribution ? one(store, distribution, DCAT_DOWNLOAD_URL) : null
  const label = one(store, dataset, RDFS_LABEL)
  const comment = one(store, dataset, RDFS_COMMENT)
  // Without a download URL, the dataset IRI is the best place to fetch it from
  const url = downloadUrl ? downloadUrl.value : dataset.value
  return {
    id: oneText(store, dataset, DCTERMS_IDENTIFIER),
    iri: dataset.value,
    downloadUrl: url,
    label: label ? label.value : null,
    comment: comment ? comment.value : null,
  }
}

// --- Embedded points: from the tss:points JSON to RDF -------------------------
// tss:points is a JSON text, e.g. [ { "time": "2021-03-03T23:15:00Z", "ec20": "4797.72" } ].
// We do not read that JSON ourselves. We tell a JSON-LD parser what each key means
// (a "context", built from the fields), and the parser turns the JSON into triples:
//   _:b0  ex:zevenaarshaven-time-field  "2021-03-03T23:15:00Z"^^xsd:dateTime .
//   _:b0  ex:zevenaarshaven-ec20-value-field  "4797.72"^^xsd:double .
// One blank node per point; the predicate is the field; the object is the typed value.
// The backend does exactly the same in Python (tss2rdf.points_context and lift_points).

// The JSON-LD context for the points of a source: for each field with a column name,
//   "<column name>": { "@id": "<field IRI>", "@type": "<its tss:datatype>" }
// or, for rdf:langString, { "@id": ..., "@language": "<its tss:language>" }.
// "@vocab" catches the keys no field knows (see UNMAPPED).
//
// Throws an Error when the fields can't give a clear context. These are the rules (and the
// messages) of points_context in code/TSS2RDF/tss2rdf.py, so that the browser and the backend
// refuse the same metadata, instead of the browser quietly picking one of two meanings:
//   - a field with more than one tss:columnName or tss:datatype (or tss:language, for rdf:langString)
//   - a field with a column name that is a blank node: a value needs a field IRI as its predicate
//   - two fields with the same column name: a value with that key could belong to either
//   - a column name that starts with @: JSON-LD would read that key as a keyword
//   - a tss:datatype that is not an IRI
// One more rule only the browser has: a field with the column name '__proto__' (see the end).
export function pointsContext(store, source) {
  // Object.create(null): an object without the usual JavaScript extras (no prototype), so
  // every column name is a plain key, also '__proto__'. On a normal {} object,
  // context['__proto__'] = ... would not add a key, and Object.hasOwn would not see it.
  const context = Object.create(null)
  context['@vocab'] = UNMAPPED
  let protoField = null // the field with the column name '__proto__', if there is one
  // In the same order as the Python lift (blank nodes first, then by IRI), so that when
  // several fields are wrong, both lifts name the same problem
  const fields = all(store, source, TSS_HAS_FIELD)
  fields.sort(compareTerms)

  for (const field of fields) {
    const columnName = single(store, field, TSS_COLUMN_NAME)
    // A field without a column name has no key in the points
    if (columnName === null) continue
    const name = columnName.value
    if (field.termType !== 'NamedNode') {
      throw new Error(`The field with tss:columnName '${name}' is a blank node, but the values of ` +
        'embedded points need a field IRI as their predicate.')
    }
    // Object.hasOwn: is this name already a key of the context (made from another field)?
    if (Object.hasOwn(context, name)) {
      throw new Error(`Two fields have tss:columnName '${name}', so a point's '${name}' value could belong to either.`)
    }
    if (name.startsWith('@')) {
      throw new Error(`The field ${shortName(field)} has tss:columnName '${name}': a key that starts with @ ` +
        'is a JSON-LD keyword, so it can\'t be the key of a value in tss:points.')
    }

    const definition = { '@id': field.value }
    const datatype = single(store, field, TSS_DATATYPE)
    if (datatype !== null && datatype.termType === 'NamedNode' && datatype.value === RDF_LANG_STRING) {
      // Text with a language tag: the tag comes from tss:language (none given: no tag)
      const language = single(store, field, TSS_LANGUAGE)
      if (language !== null) definition['@language'] = language.value
    } else if (datatype !== null) {
      if (datatype.termType !== 'NamedNode') {
        throw new Error(`The tss:datatype of field ${shortName(field)} must be an IRI, not ${termText(datatype)}.`)
      }
      definition['@type'] = datatype.value
    }
    context[name] = definition
    if (name === '__proto__') protoField = field
  }

  // '__proto__' is a special word in JavaScript. The JSON-LD parser copies the context into
  // normal objects, where that key gets lost, so the parser would quietly treat the field's
  // values as unknown keys (UNMAPPED) and the tables would show them as missing. We say so
  // instead. (The Python lift reads such values. This check comes after the loop, so that
  // metadata Python refuses, such as two fields named '__proto__', gets Python's message.)
  if (protoField !== null) {
    throw new Error(`The field ${shortName(protoField)} has tss:columnName '__proto__': the JSON-LD parser in ` +
      'the browser can\'t use that key (a special word in JavaScript), so its values can\'t be read here. ' +
      'The backend reads them.')
  }
  return context
}

// The one object of (subject, predicate), or null when there is none. More than one is an
// error: the metadata does not say which one is meant. (single() in tss2rdf.py does the same.)
function single(store, subject, predicate) {
  const objects = all(store, subject, predicate)
  if (objects.length > 1) {
    throw new Error(`${shortName(subject)} states ${objects.length} values for ${shortName(predicate)}; only one is allowed.`)
  }
  return objects.length === 1 ? objects[0] : null
}

// Blank nodes before IRIs, then by their text (for the order of pointsContext)
function compareTerms(a, b) {
  const blankA = a.termType === 'BlankNode'
  const blankB = b.termType === 'BlankNode'
  if (blankA && !blankB) return -1
  if (!blankA && blankB) return 1
  if (a.value < b.value) return -1
  if (a.value > b.value) return 1
  return 0
}

// A JSON key that starts with "@" (or with \u0040, the same character written as a JSON
// escape), such as "@context" or "@id": a JSON-LD keyword. The text between the quotes
// may hold escapes like \" (the (\\.|[^"\\])* part); a key is followed by a colon.
// The same pattern as KEYWORD_KEY in code/TSS2RDF/tss2rdf.py.
const KEYWORD_KEY = /"(@|\\u0040)(\\.|[^"\\])*"[ \t\r\n]*:/

// Turn the tss:points of a source into RDF. Gives a Promise of a new N3 Store with one
// blank node per point. Values with empty text ("") are left out: like a missing key,
// null, or an empty CSV cell, they mean "no value". A value written as a JSON string keeps
// its text exactly, e.g. "...Z" or even a wrong " 12 " (the Python lift does the same).
//
// A key that is no field's column name gets a predicate under UNMAPPED, e.g. "extra" ->
// urn:tss:unmapped:extra. When that is not a valid IRI (a key with a space, like "Some Key",
// or with one of < > " { } | \ ` [ ]), the parser here leaves the key out without a word;
// the Python lift keeps it, as urn:tss:unmapped:Some%20Key. Only the SHACL warning (made by
// the backend) sees the difference: the tables here never show unmapped keys.
//
// The Promise is rejected, with the reason as its message, when the points can't be read:
// not exactly one rdf:JSON literal, a key that starts with @, fields that can't give a
// context (see pointsContext), text that is no JSON-LD, or a point with more than one value
// for a field (see pointRows).
export function liftPoints(store, source) {
  const points = all(store, source, TSS_POINTS)
  if (points.length !== 1) {
    return Promise.reject(new Error(`${shortName(source)} has ${points.length} tss:points values; expected exactly one`))
  }
  const literal = points[0]
  if (literal.termType !== 'Literal' || literal.datatype.value !== RDF_JSON) {
    return Promise.reject(new Error(`The tss:points of ${shortName(source)} is not an rdf:JSON literal`))
  }
  // Points hold values only. A JSON-LD keyword as a key would change how they are read:
  // "@context" would replace the context made from the fields (and make the parser
  // download the address it names), "@id" would turn a point into another resource.
  // So a key that starts with @ is refused. This is a safety check on the literal's text,
  // not a way of reading the points: the JSON-LD parser below reads them.
  if (KEYWORD_KEY.test(literal.value)) {
    return Promise.reject(new Error(`The tss:points of ${shortName(source)} has a JSON key that starts with @ ` +
      '(a JSON-LD keyword such as @context or @id). Points only hold values, keyed by the fields\' tss:columnName.'))
  }

  // The context made from the fields. pointsContext throws when the fields can't make one
  // (e.g. two fields with the same column name); that becomes a rejected Promise, so the
  // caller's .catch shows the reason, as for every other problem with the points.
  let context
  try {
    context = pointsContext(store, source)
  } catch (error) {
    return Promise.reject(error)
  }

  // The context goes to the parser "out of band": next to the JSON, not inside it,
  // so the points text is parsed exactly as it is written in the metadata.
  const options = { '@comunica/actor-rdf-parse-jsonld:parserOptions': { context: context } }
  // The base IRI: the source's IRI (the points only hold values, so nothing in them
  // is normally resolved against it)
  return parseRdf(literal.value, 'application/ld+json', source.value, options).then((parsed) => {
    dropEmptyValues(parsed.store)
    // pointRows runs here only as a check, and its rows are thrown away: it throws for a
    // point that no table can show (two values for one field, e.g. a JSON array as a value),
    // and a throw inside .then rejects the Promise. The Python converter and /query refuse
    // such points too. So whoever lifts the points gets the reason through its .catch.
    // The rows the tabs draw are made again by readPoints (called once by App.jsx): no tab
    // calls pointRows while it draws, where a throw would stop the whole page.
    pointRows(parsed.store, store, source)
    return parsed.store
  })
}

// Remove every triple whose object is a literal with empty text
function dropEmptyValues(store) {
  for (const quad of store.getQuads(null, null, null, null)) {
    if (quad.object.termType === 'Literal' && quad.object.value === '') store.removeQuad(quad)
  }
}

// The lifted points as table rows, ordered by time (blank nodes have no order):
//   [{ point: BlankNode, time: Literal or null, values: { '<field IRI>': Literal } }, ...]
//   liftedStore: the store from liftPoints; store and source: the metadata, for its fields
// Only the source's fields are read: keys no field knows (predicates under UNMAPPED, or a
// key with a colon like "a:b", which JSON-LD reads as an IRI of its own) are left out.
// Throws an Error when a point has more than one value for a field (a JSON array as a
// value, e.g. "v": ["1.5", "9.9"]): a cell can show only one, and keeping the first would
// hide the others. The Python lift says the same (read_points in tss2rdf.py, point_rows in
// backend/data_reading.py). liftPoints already makes this check, so its store never makes
// pointRows throw.
export function pointRows(liftedStore, store, source) {
  const timeField = one(store, source, TSS_TIME_FIELD)
  const fieldIris = all(store, source, TSS_HAS_FIELD).map((field) => field.value)
  const rows = []
  for (const point of liftedStore.getSubjects(null, null, null)) {
    // A JSON object inside a point would be a blank node too, but it is not a point:
    // points are the subjects that are not the object of another triple
    if (liftedStore.countQuads(null, null, point, null) > 0) continue

    const values = {}
    for (const quad of liftedStore.getQuads(point, null, null, null)) {
      const fieldIri = quad.predicate.value
      if (!fieldIris.includes(fieldIri)) continue
      // Object.hasOwn: does this point already have a value for this field?
      if (Object.hasOwn(values, fieldIri)) {
        // The field by its column name, or by its IRI when it has none (as column_name in backend/source_reading.py)
        let name = oneText(store, fieldIri, TSS_COLUMN_NAME)
        if (name === '') name = shortName(fieldIri)
        throw new Error(`A point in tss:points has more than one '${name}' value.`)
      }
      values[fieldIri] = quad.object
    }
    // A point with only unknown keys has nothing to show
    if (Object.keys(values).length === 0) continue

    const time = timeField && values[timeField.value] ? values[timeField.value] : null
    rows.push({ point: point, time: time, values: values })
  }
  rows.sort(compareRowTimes)
  return rows
}

// The embedded points of a source, read once for every tab: App.jsx calls this while it loads
// the document and puts the answer on the rdf prop (rdf.points). Gives a Promise of
//   null                                the source is not embedded (tss:embedded is not true)
//   { store, rows, error: null }        store: the points as RDF (liftPoints), rows: pointRows of it
//   { store: null, rows: null, error }  the points can't be read: error says why (a text)
// The Promise is never rejected: the tabs show a problem with the points, not App.
export function readPoints(store, source) {
  if (isEmbedded(store, source) !== true) return Promise.resolve(null)
  return liftPoints(store, source)
    .then((lifted) => ({ store: lifted, rows: pointRows(lifted, store, source), error: null }))
    .catch((err) => ({ store: null, rows: null, error: err.message }))
}

// Earlier times first; rows without a readable time last. A time field may also
// hold numbers (e.g. seconds), so numbers are compared too.
function compareRowTimes(a, b) {
  const timeA = orderValue(a.time)
  const timeB = orderValue(b.time)
  if (timeA === null && timeB === null) return 0
  if (timeA === null) return 1
  if (timeB === null) return -1
  return timeA - timeB
}

function orderValue(term) {
  const time = timeValue(term)
  if (time !== null) return time
  return numberValue(term)
}
