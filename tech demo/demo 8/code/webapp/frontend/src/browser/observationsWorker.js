// observationsWorker.js: the RDF generation tab's work, done in a Web Worker so the page stays
// responsive (a big file gives a million triples and more). observations.js starts this worker.
//
// It is a port of generate() in code/TSS2RDF/tss2rdf.py, the converter the backend runs: the
// same SOSA observations, the same rules and the same error messages. Every row of the data gives
// one sosa:Observation for each field with a tss:observedProperty (except the time field):
//
//     _:o001 a sosa:Observation ;
//         dcterms:source            <the tss:Source> ;                     (when it has an IRI)
//         sosa:observedProperty     <the field's tss:observedProperty> ;
//         sosa:madeBySensor         <the field's tss:sensor> ;             (when stated)
//         sosa:hasFeatureOfInterest <the field's tss:featureOfInterest> ;  (when stated)
//         qudt:hasUnit              <the field's tss:unit> ;               (when stated)
//         sosa:resultTime           "<the row's time>"^^xsd:dateTime ;
//         sosa:hasSimpleResult      "<the value>"^^<the field's tss:datatype> .
//
// - Every observation links back to its source (dcterms:source), not to what the metadata says about it.
// - Only rows inside the source's time window: tss:from included, tss:until not.
// - An empty value gives no observation; a value that does not fit its datatype stops the work.
// - CSV values keep their text exactly as it is in the file.
// - What the metadata says about the IRIs the observations use (a sensor, a feature, ...) is
//   copied along, without the TSS vocabulary, with new blank nodes.
// The RDF syntax is written by n3's Writer: N-Triples for the whole graph, Turtle for a preview
// of the first 200 observations.
//
// Messages (see observations.js):
//   in:  { type: 'start', metadataText, contentType, baseIRI, dataFile }
//   out: { type: 'parquet' }                  "send me the rows of the Parquet file" (DuckDB runs
//   in:  { type: 'parquet', names, rows }      on the page, not in this worker), answered with them
//        or { type: 'parquet', error }
//   out: { type: 'done', result } or { type: 'error', message }

import { DataFactory, Store, Writer } from 'n3'
import { parseRdf } from '../rdf.js'
import {
  DCTERMS,
  QUDT,
  RDF,
  RDF_LANG_STRING,
  RDF_TYPE,
  SOSA,
  TSS,
  TSS_COLUMN_INDEX,
  TSS_COLUMN_NAME,
  TSS_DATA_FILE,
  TSS_DATATYPE,
  TSS_DELIMITER,
  TSS_EMBEDDED,
  TSS_ENCODING,
  TSS_FEATURE_OF_INTEREST,
  TSS_FORMAT,
  TSS_HAS_FIELD,
  TSS_HEADER,
  TSS_LANGUAGE,
  TSS_OBSERVED_PROPERTY,
  TSS_PARQUET,
  TSS_CSV,
  TSS_SENSOR,
  TSS_SOURCE,
  TSS_TIME_FIELD,
  TSS_UNIT,
  XSD,
  XSD_DATE_TIME,
  XSD_DATE_TIME_STAMP,
  pointStore,
  pointRows,
} from '../tss.js'
import { compareCodePoints, pyRepr, rdflibPrefixes, termTextWith } from './python.js'
import { hasForm, isoMoment, parseFormTime, pythonBoolean } from './values.js'
import { columnIndex, readSource } from './sourceReading.js'
import { CsvReader, eachLine } from './csvFile.js'

const { namedNode, blankNode, literal } = DataFactory

// How many observations the Turtle preview shows (generate's preview_limit)
const PREVIEW_LIMIT = 200
// The N-Triples text is collected in pieces of about this many characters, each turned into a
// Blob, so the whole file never has to be one huge text
const PIECE_SIZE = 4 * 1024 * 1024

// The answer to the 'parquet' request, when one is waiting
let parquetAnswer = null

self.onmessage = (event) => {
  const message = event.data
  if (message.type === 'start') {
    generate(message)
      .then((result) => self.postMessage({ type: 'done', result: result }))
      .catch((error) => self.postMessage({ type: 'error', message: error.message }))
  } else if (message.type === 'parquet' && parquetAnswer !== null) {
    parquetAnswer(message)
    parquetAnswer = null
  }
}

// Ask the page for the rows of the Parquet file: { names, rows } with each value as text
function askParquetRows() {
  return new Promise((resolve, reject) => {
    parquetAnswer = (message) => (message.error ? reject(new Error(message.error)) : resolve(message))
    self.postMessage({ type: 'parquet' })
  })
}

// ---------------------------------------------------------------------------
// Reading the metadata (tss2rdf.read_source, which refuses a property stated twice)
// ---------------------------------------------------------------------------

// The one value of a property, or null. More than one value is an error. (tss2rdf.single)
function single(store, node, property) {
  const values = store.getObjects(node, namedNode(property), null)
  if (values.length > 1) {
    throw new Error(`${node.value} states ${values.length} values for ${property}; only one is allowed.`)
  }
  return values.length > 0 ? values[0] : null
}

// A term as rdflib's term.n3() writes it without prefixes: <https://...>, "x"^^<...>
function fullText(term) {
  return termTextWith(term, {})
}

// tss:from or tss:until as microseconds, or null when it is not stated (tss2rdf.window_moment:
// rdflib reads an xsd:dateTime literal with Python's datetime.fromisoformat)
function converterWindowMoment(store, node, name) {
  const value = single(store, node, TSS + name)
  if (value === null) return null
  let moment = null
  if (value.termType === 'Literal' && value.datatype.value === XSD_DATE_TIME) {
    try {
      moment = parseFormTime(value.value)
    } catch {
      moment = null
    }
  }
  if (moment === null) {
    throw new Error(`tss:${name} must be an xsd:dateTime like "2021-03-03T23:45:00Z"^^xsd:dateTime, not ${fullText(value)}.`)
  }
  return moment
}

// The source, and everything the conversion needs from it and its fields (tss2rdf.read_source)
function converterSource(store) {
  const sources = store.getSubjects(namedNode(RDF_TYPE), namedNode(TSS_SOURCE), null)
  if (sources.length !== 1) {
    throw new Error(`The metadata must describe exactly one tss:Source (found ${sources.length}).`)
  }
  const node = sources[0]

  const embedded = single(store, node, TSS_EMBEDDED)
  if (pythonBoolean(embedded) === null) throw new Error('The source must state tss:embedded true or false.')
  const timeField = single(store, node, TSS_TIME_FIELD)
  if (timeField === null) throw new Error('The source has no tss:timeField, so the observations would have no time.')

  const fields = []
  for (const field of store.getObjects(node, namedNode(TSS_HAS_FIELD), null)) {
    const column = single(store, field, TSS_COLUMN_NAME)
    fields.push({
      node: field,
      column: column !== null ? column.value : null,
      index: columnIndex(single(store, field, TSS_COLUMN_INDEX)),
      datatype: single(store, field, TSS_DATATYPE),
      language: single(store, field, TSS_LANGUAGE),
      observedProperty: single(store, field, TSS_OBSERVED_PROPERTY),
      sensor: single(store, field, TSS_SENSOR),
      featureOfInterest: single(store, field, TSS_FEATURE_OF_INTEREST),
      unit: single(store, field, TSS_UNIT),
    })
  }
  // Always the same order: by tss:columnIndex, then by column name (a stable sort, like Python's)
  fields.sort((a, b) => {
    if ((a.index === null) !== (b.index === null)) return a.index === null ? 1 : -1
    if ((a.index || 0) !== (b.index || 0)) return (a.index || 0) - (b.index || 0)
    return compareCodePoints(a.column || '', b.column || '')
  })

  let timeColumn = null
  let timeDatatype = null
  for (const field of fields) {
    if (field.node.equals(timeField)) {
      timeColumn = field.column
      timeDatatype = field.datatype
    }
  }
  if (timeColumn === null) {
    throw new Error("The tss:timeField must be one of the source's fields, with a tss:columnName.")
  }

  const delimiter = single(store, node, TSS_DELIMITER)
  const header = single(store, node, TSS_HEADER)
  const encoding = single(store, node, TSS_ENCODING)
  return {
    node: node,
    embedded: pythonBoolean(embedded),
    format: single(store, node, TSS_FORMAT),
    dataFile: single(store, node, TSS_DATA_FILE),
    delimiter: delimiter !== null ? delimiter.value : ',',
    header: header !== null ? pythonBoolean(header) !== false : true,
    encoding: encoding !== null ? encoding.value : 'utf-8',
    timeField: timeField,
    timeColumn: timeColumn,
    timeDatatype: timeDatatype, // xsd:dateTime, or xsd:dateTimeStamp (a timezone is required)
    from: converterWindowMoment(store, node, 'from'),
    until: converterWindowMoment(store, node, 'until'),
    fields: fields,
  }
}

// A date-time text as an xsd:dateTime literal (its text kept as it is), and as microseconds to
// compare with the time window (tss2rdf.read_time). datatype: the time field's tss:datatype.
function readTime(text, where, datatype) {
  if (datatype !== null && datatype.value === XSD_DATE_TIME_STAMP && datatype.termType === 'NamedNode' &&
      !hasForm(text, XSD_DATE_TIME_STAMP)) {
    throw new Error(`${where}: ${pyRepr(text)} is not an xsd:dateTimeStamp (a date-time with a timezone) ` +
      'like 2021-03-03T23:45:00Z')
  }
  if (!hasForm(text, XSD_DATE_TIME)) {
    throw new Error(`${where}: ${pyRepr(text)} is not an xsd:dateTime like 2021-03-03T23:45:00Z`)
  }
  const moment = isoMoment(text)
  if (moment === null) throw new Error(`${where}: ${pyRepr(text)} is not a valid date-time`)
  return { literal: literal(text, namedNode(XSD_DATE_TIME)), moment: moment }
}

// A value as a literal with the field's datatype, its text kept as it is (tss2rdf.value_literal).
// Stops when the text does not fit the datatype. prefixTable: the preview's prefixes, for the message.
function valueLiteral(text, field, where, prefixTable) {
  const datatype = field.datatype
  if (datatype !== null && datatype.termType === 'NamedNode' && datatype.value === RDF_LANG_STRING) {
    if (field.language === null) throw new Error(`Field ${field.column} is rdf:langString but has no tss:language.`)
    return literal(text, field.language.value)
  }
  if (datatype === null) return literal(text)
  if (datatype.termType === 'NamedNode' && !hasForm(text, datatype.value)) {
    throw new Error(`${where}: column ${field.column} has ${pyRepr(text)}, which is not a valid ${termTextWith(datatype, prefixTable)}.`)
  }
  return literal(text, namedNode(datatype.value))
}

// ---------------------------------------------------------------------------
// What the metadata says about the IRIs the observations use (tss2rdf.describe)
// ---------------------------------------------------------------------------

// rdflib's Graph.cbd: the triples of a resource, the blank nodes they point to (and theirs), and
// the reified statements about it
function addDescription(source, resource, target) {
  for (const quad of source.getQuads(resource, null, null, null)) {
    target.addQuad(quad.subject, quad.predicate, quad.object)
    if (quad.object.termType === 'BlankNode' && target.countQuads(quad.object, null, null, null) === 0) {
      addDescription(source, quad.object, target)
    }
  }
  for (const statement of source.getSubjects(namedNode(RDF + 'subject'), resource, null)) {
    for (const quad of source.getQuads(statement, null, null, null)) target.addQuad(quad.subject, quad.predicate, quad.object)
  }
}

// What the metadata says about these resources, without the TSS vocabulary, with new blank nodes
function describe(store, resources) {
  // Leave out the TSS vocabulary first (how the data is stored stays in the metadata)
  const withoutTss = new Store()
  for (const quad of store.getQuads(null, null, null, null)) {
    if (quad.predicate.value.startsWith(TSS)) continue
    if (quad.object.termType === 'NamedNode' && quad.object.value.startsWith(TSS)) continue // e.g. rdf:type tss:Field
    withoutTss.addQuad(quad.subject, quad.predicate, quad.object)
  }
  const description = new Store()
  for (const resource of resources) {
    const one = new Store()
    addDescription(withoutTss, namedNode(resource), one)
    description.addQuads(one.getQuads(null, null, null, null))
  }
  // Every blank node gets a new label of its own (blankNode() makes n3-0, n3-1, ...), so it can
  // never be the label of an observation (o001, o002, ...)
  const fresh = new Map()
  const freshTerm = (term) => {
    if (term.termType !== 'BlankNode') return term
    if (!fresh.has(term.value)) fresh.set(term.value, blankNode())
    return fresh.get(term.value)
  }
  const copy = new Store()
  for (const quad of description.getQuads(null, null, null, null)) {
    copy.addQuad(freshTerm(quad.subject), quad.predicate, freshTerm(quad.object))
  }
  return copy.getQuads(null, null, null, null)
}

// ---------------------------------------------------------------------------
// Reading the data (tss2rdf.read_rows): every row as { where, row: field IRI -> text }
// ---------------------------------------------------------------------------

// The embedded points as rows, in time order (tss2rdf.read_points)
async function converterPoints(store, source) {
  const points = pointStore(store, source.node)
  const columns = new Map() // field IRI -> its column name, for error messages
  for (const field of source.fields) columns.set(field.node.value, field.column)
  const timeIri = source.timeField.value
  const found = []
  for (const point of pointRows(points, store, source.node)) {
    const row = {}
    for (const iri of Object.keys(point.values)) {
      if (!columns.has(iri)) continue
      const value = point.values[iri]
      if (value.termType !== 'Literal') {
        const name = columns.get(iri)
        throw new Error(`A point has a node as its ${name === null ? 'None' : pyRepr(name)} value, not a literal.`)
      }
      row[iri] = value.value // the literal's text, like the text of a CSV cell
    }
    if (Object.keys(row).length === 0) continue
    // A point without a time, like a CSV row with an empty time cell, stops the conversion
    if (row[timeIri] === undefined) {
      throw new Error(`A point has no time: no ${pyRepr(source.timeColumn)} value (the tss:timeField).`)
    }
    const time = readTime(row[timeIri], `The point at ${row[timeIri]}`, source.timeDatatype)
    found.push({ moment: time.moment, row: row })
  }
  // Points have no order in RDF, so put them in time order (a stable sort, like Python's)
  found.sort((a, b) => a.moment - b.moment)
  return found.map((point) => ({ where: `The point at ${point.row[timeIri]}`, row: point.row }))
}

// Go through every row of the data: visit(where, row)
async function eachRow(store, source, dataFile, visit) {
  if (source.embedded) {
    for (const point of await converterPoints(store, source)) visit(point.where, point.row)
    return
  }

  const format = source.format !== null && source.format.termType === 'NamedNode' ? source.format.value : null
  if (format === TSS_PARQUET) {
    const table = await askParquetRows()
    // Where each field's value is: the Parquet column with its tss:columnName
    const positions = []
    for (const field of source.fields) {
      if (field.column !== null && table.names.includes(field.column)) {
        positions.push([field.node.value, table.names.indexOf(field.column)])
      }
    }
    let number = 0
    for (const found of table.rows) {
      number += 1
      const row = {}
      for (const [iri, index] of positions) row[iri] = found[index]
      visit(`Parquet row ${number}`, row)
    }
    return
  }

  if (format === TSS_CSV) {
    if (Array.from(source.delimiter).length !== 1) {
      throw new Error(`tss:delimiter must be one character, not ${pyRepr(source.delimiter)}.`)
    }
    // Python's csv module reads the file with its line endings (open(..., newline="")), so a
    // quoted cell keeps a line break that is inside it
    const reader = new CsvReader(source.delimiter)
    let positions = null
    const handle = (cells) => {
      if (positions === null) {
        positions = []
        // Where each field's cell is: the column with its tss:columnName in the header line, or,
        // without one (tss:header false), the column at its tss:columnIndex
        if (source.header) {
          for (const field of source.fields) {
            if (field.column !== null && cells.includes(field.column)) positions.push([field.node.value, cells.indexOf(field.column)])
          }
          return
        }
        for (const field of source.fields) {
          if (field.index !== null) positions.push([field.node.value, field.index])
        }
      }
      const row = {}
      for (const [iri, index] of positions) {
        if (index < cells.length) row[iri] = cells[index]
      }
      visit(`CSV line ${reader.lineNumber}`, row)
    }
    await eachLine(dataFile, (text, ending) => {
      const cells = reader.feed(text + ending)
      if (cells !== null) handle(cells)
    }, source.encoding)
    const last = reader.finish()
    if (last !== null) handle(last)
    return
  }

  throw new Error(`Can't read data in tss:format ${source.format !== null ? source.format.value : 'None'}; ` +
    'this converter reads tss:CSV, tss:Parquet and embedded points.')
}

// ---------------------------------------------------------------------------
// Writing the observations (tss2rdf.generate)
// ---------------------------------------------------------------------------

async function generate({ metadataText, contentType, baseIRI, dataFile }) {
  const parsed = await parseRdf(metadataText, contentType, baseIRI)
  const store = parsed.store

  // First what the backend's route checks (rdf_routes.generate_rdf): one source, and its data file
  const sources = store.getSubjects(namedNode(RDF_TYPE), namedNode(TSS_SOURCE), null)
  if (sources.length === 0) throw new Error('The metadata has no tss:Source')
  if (sources.length > 1) {
    throw new Error(`The metadata has ${sources.length} tss:Source nodes; this dashboard reads one source per endpoint`)
  }
  const routeSource = readSource(store, sources[0])
  if (routeSource.embedded !== true) {
    if (routeSource.dataFile === null) throw new Error('The source has no tss:dataFile.')
    if (!dataFile) throw new Error(`Data file not found: ${routeSource.dataFile}`)
  }

  const source = converterSource(store)
  const measured = [] // the fields that become observations
  for (const field of source.fields) {
    if (field.observedProperty !== null && !field.node.equals(source.timeField)) {
      if (field.column === null) throw new Error(`Field ${field.node.value} has a tss:observedProperty but no tss:columnName.`)
      measured.push(field)
    }
  }
  if (measured.length === 0) {
    throw new Error('No field has a tss:observedProperty, so there is nothing to turn into observations.')
  }
  if (!source.embedded && !dataFile) {
    throw new Error('The source keeps its data in a file (tss:dataFile), but no data file was given.')
  }

  // The IRIs the observations point to: their properties, sensors, features and units,
  // and what the metadata says about them
  const referenced = new Set()
  for (const field of measured) {
    for (const key of ['observedProperty', 'sensor', 'featureOfInterest', 'unit']) {
      if (field[key] !== null && field[key].termType === 'NamedNode') referenced.add(field[key].value)
    }
  }
  const description = describe(store, referenced)

  // Every IRI in the output (and every datatype), to choose the prefixes of the preview: the
  // source's too, which every observation links back to (a blank node source gets no link)
  const used = new Set(referenced)
  const sourceLink = source.node.termType === 'NamedNode' ? source.node : null
  if (sourceLink !== null) used.add(sourceLink.value)
  for (const field of measured) {
    if (field.datatype !== null && field.datatype.termType === 'NamedNode') used.add(field.datatype.value)
  }
  for (const quad of description) {
    for (const term of [quad.subject, quad.predicate, quad.object]) {
      if (term.termType === 'NamedNode') used.add(term.value)
      if (term.termType === 'Literal') used.add(term.datatype.value)
    }
  }
  // The preview's prefixes: rdf, dcterms, sosa, qudt and xsd, plus the metadata's own prefixes
  // (and rdflib's, which the backend's graph knows too) that these IRIs use
  const previewPrefixes = { rdf: RDF, dcterms: DCTERMS, sosa: SOSA, qudt: QUDT, xsd: XSD }
  const known = rdflibPrefixes(parsed.prefixes)
  for (const prefix of Object.keys(known)) {
    const namespace = known[prefix]
    if (prefix === '' || namespace === TSS || Object.hasOwn(previewPrefixes, prefix)) continue
    for (const iri of used) {
      if (iri.startsWith(namespace)) {
        previewPrefixes[prefix] = namespace
        break
      }
    }
  }

  // What every observation of a field says, whatever the row: (predicate, object) pairs.
  // The first ones: its type, and the link back to its source.
  const fixed = new Map()
  for (const field of measured) {
    const pairs = [[namedNode(RDF_TYPE), namedNode(SOSA + 'Observation')]]
    if (sourceLink !== null) pairs.push([namedNode(DCTERMS + 'source'), sourceLink])
    for (const [property, key] of [['observedProperty', 'observedProperty'], ['madeBySensor', 'sensor'],
      ['hasFeatureOfInterest', 'featureOfInterest']]) {
      if (field[key] !== null) pairs.push([namedNode(SOSA + property), field[key]])
    }
    if (field.unit !== null) pairs.push([namedNode(QUDT + 'hasUnit'), field.unit])
    fixed.set(field.node.value, pairs)
  }
  const resultTime = namedNode(SOSA + 'resultTime')
  const simpleResult = namedNode(SOSA + 'hasSimpleResult')

  const series = measured.map((field) => ({ field: field.node.value, observations: 0 }))
  // Each observation is a blank node _:o001, _:o002, ... (the zeros keep the preview in data order)
  const digits = String(PREVIEW_LIMIT).length

  // n3's Writer in N-Triples mode writes one line per triple; the lines are collected in
  // pieces, and each piece becomes a Blob
  const lineWriter = new Writer({ format: 'N-Triples' })
  const pieces = []
  let text = ''
  const preview = [] // the triples of the first observations, and the description
  let observations = 0
  let triples = 0
  const timeIri = source.timeField.value

  await eachRow(store, source, dataFile, (where, row) => {
    const time = row[timeIri] === undefined ? '' : row[timeIri]
    if (time === '') throw new Error(`${where}: no time in column ${source.timeColumn} (the tss:timeField).`)
    const reading = readTime(time, `${where}, column ${source.timeColumn}`, source.timeDatatype)
    if (source.from !== null && reading.moment < source.from) return
    if (source.until !== null && reading.moment >= source.until) return

    for (let index = 0; index < measured.length; index++) {
      const field = measured[index]
      const cell = row[field.node.value] === undefined ? '' : row[field.node.value]
      if (cell === '') continue // an empty value: no observation
      const value = valueLiteral(cell, field, where, previewPrefixes)
      observations += 1
      const observation = blankNode('o' + String(observations).padStart(digits, '0'))

      const statements = []
      for (const [property, object] of fixed.get(field.node.value)) statements.push([observation, property, object])
      statements.push([observation, resultTime, reading.literal])
      statements.push([observation, simpleResult, value])
      for (const [subject, predicate, object] of statements) {
        text += lineWriter.quadToString(subject, predicate, object)
        if (observations <= PREVIEW_LIMIT) preview.push(DataFactory.quad(subject, predicate, object))
      }
      triples += statements.length
      series[index].observations += 1
      if (text.length >= PIECE_SIZE) {
        pieces.push(new Blob([text]))
        text = ''
      }
    }
  })

  // What the metadata says about the sensors, features, ... : at the end, and in the preview
  for (const quad of description) {
    text += lineWriter.quadToString(quad.subject, quad.predicate, quad.object)
    preview.push(quad)
  }
  triples += description.length
  pieces.push(new Blob([text]))
  const ntriples = new Blob(pieces, { type: 'application/n-triples' })

  return {
    observations: observations,
    triples: triples,
    series: series,
    previewObservations: Math.min(observations, PREVIEW_LIMIT),
    previewTruncated: observations > PREVIEW_LIMIT,
    bytes: ntriples.size,
    previewTurtle: await previewTurtle(preview, previewPrefixes),
    ntriples: ntriples,
  }
}

// The preview as Turtle, written by n3, with only the prefixes it uses (rdf:type is written
// as "a", so it needs no rdf: prefix, as in rdflib's preview)
function previewTurtle(quads, prefixes) {
  const iris = new Set()
  for (const quad of quads) {
    for (const term of [quad.subject, quad.predicate, quad.object]) {
      if (term === quad.predicate && term.value === RDF_TYPE) continue
      if (term.termType === 'NamedNode') iris.add(term.value)
      // (a literal with a language tag is written "text"@nl: its datatype rdf:langString is not written)
      if (term.termType === 'Literal' && !term.language) iris.add(term.datatype.value)
    }
  }
  const usedPrefixes = {}
  for (const prefix of Object.keys(prefixes)) {
    for (const iri of iris) {
      if (iri.startsWith(prefixes[prefix])) {
        usedPrefixes[prefix] = prefixes[prefix]
        break
      }
    }
  }
  return new Promise((resolve, reject) => {
    const writer = new Writer({ prefixes: usedPrefixes, format: 'text/turtle' })
    writer.addQuads(quads)
    writer.end((error, turtle) => (error ? reject(error) : resolve(turtle)))
  })
}
