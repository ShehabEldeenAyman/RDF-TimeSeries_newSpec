// appendPoints.js: appending new points (readings) to an embedded endpoint, here in the browser tab.
//
// The points of an embedded source are RDF nodes at the end of its metadata document, each one
// Turtle statement (see the points section of tss.js). So a new reading is one more statement,
// appended after the last line of the document:
//   [] tss:pointOf ex:zevenaarshaven-tv-source ;
//       ex:zevenaarshaven-tv-time-field "2021-03-04T01:15:00Z"^^xsd:dateTime ;
//       ex:zevenaarshaven-tv-value-field "4775.96"^^xsd:double .
// Nothing before it changes, which a JSON literal (the old tss:points) could not do.
//
// This demo never changes a file on the server. Appending to a bundled endpoint makes a copy of it
// in this browser tab (App.jsx lists it in the drop down, like your own endpoints, and every tab
// computes its answers in the browser, see dataApi.js); appending to a copy or to your own endpoint
// changes that one, in this tab. A reload of the page forgets them.
//
// Usage (the "Add data" tab, AddPointForm.jsx, and the "Append example" tab, AppendExample.jsx):
//   canAppend(rdf)                              // may points be appended here? (see there)
//   const fields = pointFields(rdf)             // the fields a point can have a value for, the time first
//   const values = exampleValues(rdf, fields)   // a pre-made reading: { '<field IRI>': '<text>', ... }
//   const text = pointStatement(rdf, values)    // the Turtle statement of one point
//   appendText(rdf, baseIRI, text)              // the document with the text appended, parsed
//     .then(({ document, before, after }) => ...)   // before/after: the number of points
//     .catch((err) => ...)                          // err.message says why it can't be appended

import { parseRdf } from './rdf'
import { fieldLabel } from './fields'
import {
  one, oneText, readFields, isEmbedded, isMutable, isNumericDatatype, findSource, pointNodes, quoteText, timeValue,
  RDF_LANG_STRING, TSS_COLUMN_NAME, TSS_DATATYPE, TSS_FROM, TSS_LANGUAGE, TSS_POINT_OF, TSS_TIME_FIELD, TSS_UNTIL,
} from './tss'

// The syntaxes a Turtle statement can be appended to: Turtle, and N3 (which reads Turtle too)
const TURTLE_TYPES = ['text/turtle', 'application/x-turtle', 'text/n3']

// The time between two points of the pre-made example when the points don't say (15 minutes)
const DEFAULT_STEP = 15 * 60 * 1000

// May new points be appended to this document? Only when its source says tss:embedded true (the
// points are in the metadata) and tss:mutable true (its data may change), when the source has an
// IRI (a new point must name it), and when the document is Turtle (or N3): a Turtle statement
// can't be appended to N-Triples or RDF/XML.
//   rdf: the prop every tab gets (see App.jsx)
export function canAppend(rdf) {
  return isEmbedded(rdf.store, rdf.source) === true &&
    isMutable(rdf.store, rdf.source) === true &&
    rdf.source.termType === 'NamedNode' &&
    TURTLE_TYPES.includes(rdf.contentType)
}

// The fields a point can hold a value for, the time field first, each as
// { iri, label, columnName, datatype, numeric, isTime }. A field must have an IRI: a point names
// it as the predicate of its value (a blank node field can't be named in an appended statement).
export function pointFields(rdf) {
  const timeField = one(rdf.store, rdf.source, TSS_TIME_FIELD)
  const found = []
  for (const field of readFields(rdf.store, rdf.source)) {
    if (field.termType !== 'NamedNode') continue
    const datatype = one(rdf.store, field, TSS_DATATYPE)
    const isTime = timeField !== null && field.value === timeField.value // IRIs compared in full
    found.push({
      iri: field.value,
      label: fieldLabel(rdf.store, field, rdf.prefixes),
      columnName: oneText(rdf.store, field, TSS_COLUMN_NAME),
      datatype: datatype,
      numeric: !isTime && isNumericDatatype(datatype),
      isTime: isTime,
    })
  }
  // The time field first, the others in the order of readFields
  found.sort((a, b) => (a.isTime === b.isTime ? 0 : a.isTime ? -1 : 1))
  return found
}

// A pre-made reading, to append as an example: one step after the newest point, with the values of
// the newest point (so they fit the fields' datatypes and ranges). The step is the time between the
// two newest points (DEFAULT_STEP when there are fewer than two). When that would reach tss:until
// (which is not in the window), the time is halfway between the newest point and tss:until.
// Without points: the time is tss:from (or now), and the other values are left empty.
// Gives { '<field IRI>': '<text>', ... } (an empty text: no value).
export function exampleValues(rdf, fields) {
  const values = {}
  for (const field of fields) values[field.iri] = ''
  const time = fields.find((field) => field.isTime)
  if (time === undefined) return values

  // The points with a readable time, oldest first (rdf.points.rows is in time order)
  const rows = rdf.points && !rdf.points.error ? rdf.points.rows : []
  const timed = rows.filter((row) => timeValue(row.time) !== null)
  if (timed.length === 0) {
    const from = one(rdf.store, rdf.source, TSS_FROM)
    values[time.iri] = from !== null ? from.value : timeText(Date.now())
    return values
  }

  const newest = timed[timed.length - 1]
  let step = DEFAULT_STEP
  if (timed.length > 1) {
    const between = timeValue(newest.time) - timeValue(timed[timed.length - 2].time)
    if (between > 0) step = between
  }
  let next = timeValue(newest.time) + step
  const until = timeValue(one(rdf.store, rdf.source, TSS_UNTIL))
  if (until !== null && next >= until) next = timeValue(newest.time) + (until - timeValue(newest.time)) / 2
  values[time.iri] = timeText(next)

  for (const field of fields) {
    const value = newest.values[field.iri]
    if (!field.isTime && value !== undefined && value.termType === 'Literal') values[field.iri] = value.value
  }
  return values
}

// A moment (milliseconds since 1970) as an xsd:dateTime in UTC, to the second: 2021-03-04T01:15:00Z
// (toISOString gives 2021-03-04T01:15:00.000Z: the milliseconds are left out)
export function timeText(milliseconds) {
  return new Date(milliseconds).toISOString().slice(0, 19) + 'Z'
}

// The current time in UTC, to the second, e.g. 2026-10-30T09:30:12Z (the form's "now" button)
export function nowText() {
  return timeText(Date.now())
}

// The Turtle statement of one point, with these values: { '<field IRI>': '<text>', ... }. A field
// with an empty text (or none) gets no value. Each value is a literal typed with its field's
// tss:datatype (rdf:langString: with its tss:language tag; no datatype: a plain text). The IRIs
// are written with the document's own prefixes, so the statement reads like the rest of the file.
export function pointStatement(rdf, values) {
  const parts = [turtleName(TSS_POINT_OF, rdf.prefixes) + ' ' + turtleName(rdf.source.value, rdf.prefixes)]
  for (const field of pointFields(rdf)) {
    const text = values[field.iri]
    if (text === undefined || text === '') continue
    parts.push(turtleName(field.iri, rdf.prefixes) + ' ' + literalTurtle(rdf, field, text))
  }
  return '[] ' + parts.join(' ;\n    ') + ' .\n'
}

// A value as a Turtle literal, typed like its field says
function literalTurtle(rdf, field, text) {
  const quoted = quoteText(text)
  if (field.datatype === null || field.datatype.termType !== 'NamedNode') return quoted
  if (field.datatype.value === RDF_LANG_STRING) {
    const language = one(rdf.store, field.iri, TSS_LANGUAGE)
    return language !== null ? quoted + '@' + language.value : quoted
  }
  return quoted + '^^' + turtleName(field.datatype.value, rdf.prefixes)
}

// An IRI as Turtle: a prefixed name with one of the document's own prefixes (the longest that
// fits), else the full IRI in < >. Only the document's prefixes: the appended text is parsed as
// part of the document, so a prefix the document does not declare would be an error.
// (metadataUpdate.js writes the whole document with it too.)
export function turtleName(iri, prefixes) {
  let bestPrefix = null
  let bestNamespace = ''
  for (const prefix of Object.keys(prefixes)) {
    const namespace = prefixes[prefix]
    if (iri.startsWith(namespace) && namespace.length > bestNamespace.length && isLocalName(iri.slice(namespace.length))) {
      bestPrefix = prefix
      bestNamespace = namespace
    }
  }
  if (bestPrefix === null) return '<' + iri + '>'
  return bestPrefix + ':' + iri.slice(bestNamespace.length)
}

// Can this text stand after "prefix:" in Turtle? Letters, digits and _ - . in the middle, but not
// "-" or "." first and not "." last (Turtle allows more; this careful subset is enough here).
function isLocalName(text) {
  return /^([A-Za-z0-9_]([A-Za-z0-9_.-]*[A-Za-z0-9_-])?)?$/.test(text)
}

// The document with this text appended after its last line, parsed again, with the same base IRI.
// Gives a Promise of { document, before, after, milliseconds }:
//   document: like a loaded document ({ store, prefixes, text, url: null, baseIRI, contentType })
//   before, after: the number of points of the source before and after the append
//   milliseconds: how long the append took: the text added, the document read again and checked
// The Promise is rejected, with the reason, when the text is no Turtle, when it adds a second
// tss:Source, or when it adds no point of this source (no new node with tss:pointOf <source>).
//   rdf: the prop every tab gets; baseIRI: the document's IRI (the endpoint's iri)
export function appendText(rdf, baseIRI, text) {
  const started = performance.now()
  const document = rdf.text.endsWith('\n') ? rdf.text : rdf.text + '\n'
  const appended = text.endsWith('\n') ? text : text + '\n'
  const newText = document + '\n' + appended
  // The line the appended text starts on, for a parse error (it names a line of the whole document)
  const firstLine = newText.slice(0, document.length + 1).split('\n').length

  return parseRdf(newText, rdf.contentType, baseIRI)
    .catch((err) => {
      throw new Error(`${err.message} (The appended text starts on line ${firstLine}.)`)
    })
    .then((parsed) => {
      const source = findSource(parsed.store)
      if (source === null || !source.equals(rdf.source)) {
        throw new Error('The appended text adds another tss:Source; a document describes exactly one.')
      }
      const before = pointNodes(rdf.store, rdf.source).length
      const after = pointNodes(parsed.store, source).length
      if (after <= before) {
        throw new Error(`The appended text adds no point of this source: a point is a node with ` +
          `${turtleName(TSS_POINT_OF, rdf.prefixes)} ${turtleName(rdf.source.value, rdf.prefixes)}.`)
      }
      return {
        document: {
          store: parsed.store,
          prefixes: parsed.prefixes,
          text: newText,
          url: null,
          baseIRI: baseIRI,
          contentType: rdf.contentType,
        },
        before: before,
        after: after,
        milliseconds: performance.now() - started,
      }
    })
}

// A duration in milliseconds as text, to a tenth: 12.3 ms (the browser measures to about 0.1 ms)
export function millisecondsText(milliseconds) {
  return milliseconds.toFixed(1) + ' ms'
}
