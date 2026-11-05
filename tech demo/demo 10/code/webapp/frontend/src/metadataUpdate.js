// metadataUpdate.js: "Update the metadata" (the button under the Add data and Append example tabs):
// recalculate, from the embedded points, what an append leaves as it was. Here in the browser tab,
// like the appends (see appendPoints.js): the file on the server never changes.
//
// An append only adds lines at the end of the document. This recalculates what is stated earlier:
//   - the window: tss:from becomes the earlier of tss:from and the oldest point, tss:until the
//     later of tss:until and the newest point plus one second (tss:until is not in the window).
//     The window never shrinks, and a side the source does not state stays open.
//   - tss:modifiedAt and tss:committedUntil: now (the metadata was edited, and the source was
//     checked to be up to date, now)
//   - every stored summary (tss:summary of a field): its window becomes the source's new window,
//     prov:generatedAtTime now, and of tss:count, tss:missing, tss:firstTime, tss:lastTime and,
//     for numeric fields, tss:observedMinimum, tss:observedMaximum, tss:mean (not rounded) and
//     tss:median, only those it already states are recomputed (tss:missingValuePolicy stays).
//     The same numbers as the Query tab's summary: "Check the stored summary" says "same" after.
// These are the rules the backend's "Add data" route used before demo 8, when it still wrote the
// file. The numbers are computed with the browser's ports of the backend's Python (values.js,
// python.js), so they have the same text as Python would write.
//
// Because those statements are not at the end, the whole document is written again as Turtle
// (documentTurtle): the same triples, with the document's prefixes, the summaries and other blank
// nodes nested as [ ... ], and the points at the end in time order. The comments of the document
// are not kept.
//
// Usage:
//   updateMetadata(rdf, baseIRI).then(({ document, changes, timings, summaries }) => ...)
//     document:  the new document, like a loaded one ({ store, prefixes, text, url: null, baseIRI, contentType })
//     changes:   [{ subject, property, before, after }]: what changed, as text (null: not stated)
//     timings:   { window, summaries, writing, total }: how long each step took, in milliseconds
//     summaries: how many stored summaries were recalculated

import { DataFactory, Store } from 'n3'
import { parseRdf } from './rdf'
import { fieldName } from './fields'
import {
  one, readFields, pointNodes, pointRows, pointStore, quoteText,
  RDF_TYPE, XSD, XSD_DATE_TIME, XSD_STRING, SUMMARY_ATTRIBUTES,
  TSS_COMMITTED_UNTIL, TSS_FROM, TSS_MODIFIED_AT, TSS_POINT_OF, TSS_SUMMARY, TSS_TIME_FIELD, TSS_UNTIL, TSS_DATATYPE,
} from './tss'
import { turtleName } from './appendPoints'
import { duckdbType, isNumeric, literalNumber, literalTime, timeText } from './browser/values.js'
import { pyFloatRepr, pySum } from './browser/python.js'

const { namedNode, literal } = DataFactory

const XSD_DOUBLE = XSD + 'double'
const XSD_DECIMAL = XSD + 'decimal'
const XSD_INTEGER = XSD + 'integer'

// One second, in the unit of the moments of values.js (microseconds since 1970)
const SECOND = 1000000

// Recalculate the window, tss:modifiedAt, tss:committedUntil and the stored summaries (see the top
// of this file). Gives a Promise of { document, changes, timings, summaries }; it is rejected with
// the reason when the points can't be read (two values for one field, a value that is no number, ...).
//   rdf: the prop every tab gets (see App.jsx); baseIRI: the document's IRI (the endpoint's iri)
export function updateMetadata(rdf, baseIRI) {
  try {
    const started = performance.now()
    // Now, to the second (2026-10-30T09:30:12Z), as a moment of values.js
    const now = Math.floor(Date.now() / 1000) * SECOND
    const store = new Store(rdf.store.getQuads(null, null, null, null)) // a copy: rdf.store stays as it is
    const source = rdf.source
    const changes = []

    // 1. The window, tss:modifiedAt and tss:committedUntil
    const rows = pointRows(pointStore(store, source), store, source)
    const timeField = one(store, source, TSS_TIME_FIELD)
    const window = newWindow(store, source, rows, timeField)
    setValue(store, source, TSS_FROM, window.from, changes, rdf)
    setValue(store, source, TSS_UNTIL, window.until, changes, rdf)
    setValue(store, source, TSS_MODIFIED_AT, timeLiteral(now), changes, rdf)
    setValue(store, source, TSS_COMMITTED_UNTIL, timeLiteral(now), changes, rdf)
    const windowDone = performance.now()

    // 2. The stored summaries
    let summaries = 0
    for (const field of readFields(store, source)) {
      const summary = one(store, field, TSS_SUMMARY)
      if (summary === null) continue
      updateSummary(store, rdf, field, summary, rows, timeField, window, now, changes)
      summaries += 1
    }
    const summariesDone = performance.now()

    // 3. The document written again, and read back (so the store and the text agree)
    const text = documentTurtle(store, rdf.prefixes, source)
    return parseRdf(text, 'text/turtle', baseIRI).then((parsed) => {
      if (parsed.store.size !== store.size) {
        throw new Error(`The document written again has ${parsed.store.size} triples instead of ${store.size}.`)
      }
      const done = performance.now()
      return {
        document: { store: parsed.store, prefixes: parsed.prefixes, text: text, url: null, baseIRI: baseIRI, contentType: 'text/turtle' },
        changes: changes,
        summaries: summaries,
        timings: {
          window: windowDone - started,
          summaries: summariesDone - windowDone,
          writing: done - summariesDone,
          total: done - started,
        },
      }
    })
  } catch (err) {
    return Promise.reject(err)
  }
}

// --- The new values ------------------------------------------------------------------

// The source's new window: { from, until }, each a literal (the current one when it does not change),
// or null when the source does not state it (an open side stays open)
function newWindow(store, source, rows, timeField) {
  const moments = []
  for (const row of rows) {
    const moment = timeField !== null ? literalTime(row.values[timeField.value]) : null
    if (moment !== null) moments.push(moment)
  }
  let from = one(store, source, TSS_FROM)
  let until = one(store, source, TSS_UNTIL)
  const currentFrom = literalTime(from)
  const currentUntil = literalTime(until)
  if (moments.length > 0) {
    const oldest = Math.min(...moments)
    const newest = Math.max(...moments)
    if (currentFrom !== null && oldest < currentFrom) from = timeLiteral(oldest)
    if (currentUntil !== null && newest + SECOND > currentUntil) until = timeLiteral(newest + SECOND)
  }
  return { from: from, until: until }
}

// Recalculate one stored summary (see the top of this file)
function updateSummary(store, rdf, field, summary, rows, timeField, window, now, changes) {
  const computed = summaryValues(rdf, field, rows, timeField, window)
  for (const attribute of SUMMARY_ATTRIBUTES) {
    if (attribute.key === 'policy') continue // tss:missingValuePolicy stays as it is
    const stored = one(store, summary, attribute.iri)
    let value
    if (attribute.key === 'from') value = window.from
    else if (attribute.key === 'until') value = window.until
    else if (attribute.key === 'generatedAt') value = timeLiteral(now)
    else if (stored === null || !(attribute.key in computed)) continue // only what the summary states
    else if (computed[attribute.key] === null) value = null // nothing to compute it from: it goes
    else if (attribute.key === 'count' || attribute.key === 'missing') value = literal(String(computed[attribute.key]), namedNode(XSD_INTEGER))
    else if (attribute.key === 'firstTime' || attribute.key === 'lastTime') value = timeLiteral(computed[attribute.key])
    else value = numberLiteral(computed[attribute.key], stored)
    if (value === null && stored === null) continue
    setValue(store, summary, attribute.iri, value, changes, rdf, field)
  }
}

// The numbers of a field's summary, from the points in the window [from, until), with the rule
// tss:ExcludeEmptyValues: count (the points with a value for the field), missing (the points
// without one), firstTime and lastTime (moments), and for a numeric field minimum, maximum, mean
// and median. A number is null when there is nothing to compute it from.
function summaryValues(rdf, field, rows, timeField, window) {
  const datatype = one(rdf.store, field, TSS_DATATYPE)
  const numeric = isNumeric(datatype)
  const start = literalTime(window.from)
  const end = literalTime(window.until)
  let count = 0
  let missing = 0
  const moments = []
  const numbers = []
  for (const row of rows) {
    const moment = timeField !== null ? literalTime(row.values[timeField.value]) : null
    // Outside the window (a point without a valid time is outside every window)
    if (moment === null || (start !== null && moment < start) || (end !== null && moment >= end)) continue
    const value = row.values[field.value]
    if (value === undefined) {
      missing += 1
      continue
    }
    count += 1
    moments.push(moment)
    if (numeric) {
      if (value.termType !== 'Literal') {
        throw new Error(`A point has a node as its '${fieldName(rdf.store, field, rdf.prefixes)}' value, not a literal.`)
      }
      numbers.push(literalNumber(value, datatype, rdf.prefixes))
    }
  }
  const values = {
    count: count,
    missing: missing,
    firstTime: moments.length > 0 ? Math.min(...moments) : null,
    lastTime: moments.length > 0 ? Math.max(...moments) : null,
  }
  if (numeric) {
    const sorted = numbers.slice().sort((a, b) => a - b)
    const middle = Math.floor(sorted.length / 2)
    values.minimum = numbers.length > 0 ? sorted[0] : null
    values.maximum = numbers.length > 0 ? sorted[sorted.length - 1] : null
    // Python's sum() and statistics.median, like the backend's summaries
    values.mean = numbers.length > 0 ? pySum(numbers) / numbers.length : null
    if (numbers.length === 0) values.median = null
    else values.median = sorted.length % 2 === 1 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2
  }
  return values
}

// A moment (values.js: microseconds since 1970) as an xsd:dateTime literal in UTC, like 2021-03-04T01:30:01Z
function timeLiteral(moment) {
  return literal(timeText(moment), namedNode(XSD_DATE_TIME))
}

// A number of a summary as a literal with the datatype the summary used for it before (stored:
// its literal): xsd:double unless it said otherwise. A value that does not fit an integer type
// (a mean of 2.5) becomes an xsd:double. The text is Python's repr (the shortest text that reads
// back as the same number), as the backend wrote it.
function numberLiteral(value, stored) {
  let datatype = stored !== null && stored.termType === 'Literal' ? stored.datatype : namedNode(XSD_DOUBLE)
  if (!isNumeric(datatype)) datatype = namedNode(XSD_DOUBLE)
  if (duckdbType(datatype) === 'BIGINT') {
    if (Number.isInteger(value)) return literal(String(value), datatype)
    datatype = namedNode(XSD_DOUBLE)
  }
  if (datatype.value === XSD_DECIMAL) return literal(decimalText(pyFloatRepr(value)), datatype)
  return literal(pyFloatRepr(value), datatype)
}

// Python's repr of a float (4750.588, 1e-05, 1e+16) as an xsd:decimal text, without an exponent
// (format(Decimal(repr(x)), "f"): 0.00001, 10000000000000000)
function decimalText(repr) {
  const parts = /^(-?)(\d+)(?:\.(\d+))?e([+-]\d+)$/.exec(repr)
  if (parts === null) return repr
  const digits = parts[2] + (parts[3] || '')
  const point = parts[2].length + Number(parts[4]) // where the decimal point goes in digits
  if (point <= 0) return parts[1] + '0.' + '0'.repeat(-point) + digits
  if (point >= digits.length) return parts[1] + digits + '0'.repeat(point - digits.length)
  return parts[1] + digits.slice(0, point) + '.' + digits.slice(point)
}

// Give (subject, property) this one value (a term), or none (null), and note the change. A time
// that is the same moment as the one stated ("...+00:00" and "...Z") is no change: it keeps its text.
//   field: for a summary, the field it belongs to (to name it in the list of changes)
function setValue(store, subject, property, value, changes, rdf, field) {
  const before = store.getObjects(subject, namedNode(property), null)
  let same = value === null ? before.length === 0 : before.length === 1 && before[0].equals(value)
  if (!same && value !== null && before.length === 1 && literalTime(value) !== null) {
    same = literalTime(before[0]) === literalTime(value)
  }
  if (same) return
  for (const old of before) store.removeQuad(subject, namedNode(property), old)
  if (value !== null) store.addQuad(subject, namedNode(property), value)
  let where = 'the source'
  if (field) where = 'the summary of ' + fieldName(rdf.store, field, rdf.prefixes)
  changes.push({
    subject: where,
    property: turtleName(property, rdf.prefixes),
    before: before.length > 0 ? before.map((term) => termTurtle(term, rdf.prefixes)).join(', ') : null,
    after: value !== null ? termTurtle(value, rdf.prefixes) : null,
  })
}

// --- Writing the document ---------------------------------------------------------------

// The whole document as Turtle: the prefixes, a note, every subject with its statements (the
// source first, then its fields, then the other IRIs, then other blank nodes), and the points at
// the end, in time order, each as one statement ([] tss:pointOf ...). A blank node that is the
// value of exactly one statement (a summary, a description, ...) is written inside it: [ ... ].
//   store: the triples; prefixes: the document's prefixes; source: the tss:Source
export function documentTurtle(store, prefixes, source) {
  const writer = new TurtleText(store, prefixes)
  const lines = []
  for (const prefix of Object.keys(prefixes)) lines.push(`@prefix ${prefix}: <${prefixes[prefix]}> .`)
  lines.push('')
  lines.push('# Written again by the dashboard ("Update the metadata", in a browser tab): the triples of the')
  lines.push('# document, without its comments. The points (tss:pointOf) are at the end, in time order.')

  const points = pointNodes(store, source)
  const isPoint = (term) => points.some((point) => point.equals(term))
  const written = [] // the subjects written so far

  // The source, its fields, the other subjects that are IRIs (by IRI), then other blank nodes
  const subjects = [source].concat(readFields(store, source))
  const others = store.getSubjects(null, null, null).filter((subject) => !isPoint(subject) && !writer.isNested(subject))
  others.sort((a, b) => (a.termType === b.termType ? (a.value < b.value ? -1 : a.value > b.value ? 1 : 0) : a.termType === 'NamedNode' ? -1 : 1))
  for (const subject of subjects.concat(others)) {
    if (written.some((term) => term.equals(subject)) || store.countQuads(subject, null, null, null) === 0) continue
    written.push(subject)
    lines.push('')
    lines.push(writer.subjectBlock(subject))
  }

  // The points, in time order (points without a field value last)
  if (points.length > 0) {
    lines.push('')
    lines.push('# Points: one RDF node per point; a new point is appended as one more statement.')
    const ordered = pointRows(pointStore(store, source), store, source).map((row) => row.point)
    for (const point of points) {
      if (!ordered.some((term) => term.equals(point))) ordered.push(point)
    }
    for (const point of ordered) {
      lines.push('')
      lines.push(writer.subjectBlock(point))
    }
  }
  return lines.join('\n') + '\n'
}

// Writes terms and statements of one store as Turtle (see documentTurtle)
class TurtleText {
  constructor(store, prefixes) {
    this.store = store
    this.prefixes = prefixes
    this.labels = new Map() // blank node -> its label in the text (_:b1, _:b2, ...)
  }

  // Is this a blank node that is the value of exactly one statement? Then it is written inside it.
  isNested(term) {
    return term.termType === 'BlankNode' && this.store.countQuads(null, null, term, null) === 1
  }

  // A subject with its statements: "<subject>\n    p o ;\n    p o ." (a blank node subject: [])
  subjectBlock(subject) {
    let head
    if (subject.termType === 'BlankNode') head = this.store.countQuads(null, null, subject, null) === 0 ? '[]' : this.label(subject)
    else head = termTurtle(subject, this.prefixes)
    const body = this.statements(subject, '    ', [subject])
    // A blank node point reads best on one line with its first statement: [] tss:pointOf ex:source ;
    if (head === '[]') return head + ' ' + body.trimStart() + ' .'
    return head + '\n' + body + ' .'
  }

  // The statements of a subject, one property per line (rdf:type first, as "a", then tss:pointOf,
  // so a point starts with its source like an appended one), each with its values separated by commas
  //   path: the blank nodes being written around this one (no endless loop through a cycle)
  statements(subject, indent, path) {
    const properties = []
    for (const quad of this.store.getQuads(subject, null, null, null)) {
      if (!properties.some((term) => term.equals(quad.predicate))) properties.push(quad.predicate)
    }
    const rank = (property) => (property.value === RDF_TYPE ? 0 : property.value === TSS_POINT_OF ? 1 : 2)
    properties.sort((a, b) => rank(a) - rank(b)) // a stable sort: the others keep their order
    const lines = []
    for (const property of properties) {
      const name = property.value === RDF_TYPE ? 'a' : termTurtle(property, this.prefixes)
      const values = this.store.getObjects(subject, property, null).map((value) => this.value(value, indent, path))
      lines.push(indent + name + ' ' + values.join(', '))
    }
    return lines.join(' ;\n')
  }

  // A value: a blank node used once is written inside, as [ ... ]; other terms as termTurtle
  value(term, indent, path) {
    if (this.isNested(term) && !path.some((node) => node.equals(term))) {
      if (this.store.countQuads(term, null, null, null) === 0) return '[]'
      return '[\n' + this.statements(term, indent + '    ', path.concat([term])) + '\n' + indent + ']'
    }
    if (term.termType === 'BlankNode') return this.label(term)
    return termTurtle(term, this.prefixes)
  }

  // A blank node's label in the text: _:b1, _:b2, ... (the same one every time)
  label(term) {
    if (!this.labels.has(term.value)) this.labels.set(term.value, '_:b' + (this.labels.size + 1))
    return this.labels.get(term.value)
  }
}

// An IRI or a literal as Turtle, with the document's prefixes: ex:source, "4797.72"^^xsd:double,
// "Observation time"@en, "green" (xsd:string is a plain text)
function termTurtle(term, prefixes) {
  if (term.termType === 'NamedNode') return turtleName(term.value, prefixes)
  if (term.termType === 'Literal') {
    const quoted = quoteText(term.value)
    if (term.language) return quoted + '@' + term.language
    if (term.datatype && term.datatype.value !== XSD_STRING) return quoted + '^^' + turtleName(term.datatype.value, prefixes)
    return quoted
  }
  return '_:' + term.value
}
