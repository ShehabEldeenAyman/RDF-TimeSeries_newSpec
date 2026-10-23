import { useState } from 'react'
import { SourceFiles } from './SourceFiles'
import { SummaryFacts } from './SummaryTable'
import { FIELD_SECTIONS, SHOWN_ELSEWHERE, SOURCE_PANEL_SKIPS, LINKABLE_VOCABULARIES } from './sections'
import { firstField, fieldLabel, fieldName, fieldText, roleText } from './fields'
import {
  all,
  has,
  one,
  oneText,
  readFields,
  isEmbedded,
  shortName,
  termText,
  dateTimeValue,
  RDFS_LABEL,
  RDFS_COMMENT,
  TSS_DATA_FILE,
  TSS_POINTS,
  TSS_TIME_FIELD,
  TSS_KEY,
  TSS_DESCRIBES_FIELD,
} from './tss'

// How each "where" is shown next to a value
const MARKERS = {
  field: '● on the field',
  source: '⬆ from the source',
  missing: '○ not stated',
}

// The series inspector: pick a field (one column of the source) and see everything the
// metadata says about it, with, for every value, where it is stated: on the field, or on
// its source. Everything is read from the parsed metadata document (rdf.store), and for
// embedded data from the points App.jsx read as RDF (rdf.points).
//   endpoint = { id, iri, downloadUrl, label, online }   rdf = { store, prefixes, text, url, source, points }
export function Inspector({ endpoint, rdf }) {
  const fields = readFields(rdf.store, rdf.source)
  const first = firstField(rdf.store, rdf.source)
  // The picked field, by its full IRI (an RDF term is a new object every time we ask the store)
  const [fieldIri, setFieldIri] = useState(first ? first.value : '')

  if (fields.length === 0) return <p className="muted">This data source has no fields.</p>

  // The picked field as an RDF term
  const field = fields.find((candidate) => candidate.value === fieldIri)

  return (
    <>
      <section className="card" id="inspector-top">
        <label>
          Field{' '}
          <select value={fieldIri} onChange={(event) => setFieldIri(event.target.value)}>
            {fields.map((option) => (
              <option key={option.value} value={option.value}>
                {fieldText(rdf.store, option, rdf.prefixes)}
              </option>
            ))}
          </select>
        </label>
        <p className="muted small legend">
          <span className="marker field">{MARKERS.field}</span> stated on this field ·{' '}
          <span className="marker source">{MARKERS.source}</span> stated on the source, so true for every field of it.
          Hover over a value to see the exact RDF.
        </p>
      </section>

      <FieldPanel rdf={rdf} fields={fields} field={field} onPick={setFieldIri} />
      <SourceFiles endpoint={endpoint} rdf={rdf} fieldIri={fieldIri} />
      <SourcePanel endpoint={endpoint} rdf={rdf} fields={fields} field={field} onPick={setFieldIri} />
    </>
  )
}

// The field's own panel: its label and comments, how it relates to the other fields, and
// the sections, with for every line where the value is stated
function FieldPanel({ rdf, fields, field, onPick }) {
  const store = rdf.store
  const sections = fieldSections(rdf, field)
  const counts = countLines(sections)

  // The fields this one says something about, and the fields that say something about this one
  const describes = all(store, field, TSS_DESCRIBES_FIELD)
  const describedBy = fields.filter((other) => !other.equals(field) && has(store, other, TSS_DESCRIBES_FIELD, field))

  return (
    <section className="card">
      <h3>{fieldLabel(store, field, rdf.prefixes)}</h3>
      {all(store, field, RDFS_COMMENT).map((comment, index) => (
        <p key={index} className="muted">{comment.value}</p>
      ))}
      <table className="facts">
        <tbody>
          <tr>
            <th>Field</th>
            <td title={field.value}>{shortName(field, rdf.prefixes)}</td>
          </tr>
          <tr>
            <th>Source</th>
            <td>
              <a href="#source-panel" title={rdf.source.value}>{shortName(rdf.source, rdf.prefixes)}</a> ({fields.length} fields)
            </td>
          </tr>
          {describes.length > 0 && (
            <tr>
              <th>Describes</th>
              <td><FieldLinks rdf={rdf} fields={fields} links={describes} onPick={onPick} /></td>
            </tr>
          )}
          {describedBy.length > 0 && (
            <tr>
              <th>Described by</th>
              <td><FieldLinks rdf={rdf} fields={fields} links={describedBy} onPick={onPick} /></td>
            </tr>
          )}
        </tbody>
      </table>

      <p className="summary">
        This field states <strong>{counts.field}</strong> of these values itself;{' '}
        <strong>{counts.source}</strong> come from its source
        {counts.missing > 0 && <>, and {counts.missing} are not stated</>}.
      </p>

      {sections.map((section) => (
        <div key={section.title} className="inspector-section">
          <h4>{section.title}</h4>
          <p className="muted small">{section.explanation}</p>
          <table className="inspector-lines">
            <tbody>
              {section.lines.map((line, index) => (
                <Line key={index} line={line} rdf={rdf} />
              ))}
            </tbody>
          </table>
        </div>
      ))}

      <div className="inspector-section">
        <h4>Stored summary</h4>
        <p className="muted small">
          What this field's <code>tss:summary</code> says: numbers computed from the data when the metadata was made,
          over the readings in [from, until). The Query tab compares them with the data now.
        </p>
        <SummaryFacts rdf={rdf} field={field} />
      </div>
    </section>
  )
}

// Fields, separated by commas. A field of this source is a link that shows it in the
// inspector (href jumps back up to the field picker); any other IRI is only text.
function FieldLinks({ rdf, fields, links, onPick }) {
  return links.map((link, index) => {
    const isOurField = fields.some((field) => field.equals(link))
    return (
      <span key={link.value}>
        {index > 0 && ', '}
        {isOurField ? (
          <a href="#inspector-top" title={link.value} onClick={() => onPick(link.value)}>
            {fieldName(rdf.store, link, rdf.prefixes)}
          </a>
        ) : (
          <span title={link.value}>{termText(link, rdf.prefixes)}</span>
        )}
      </span>
    )
  })
}

// One line: name | value | where the value is stated. Hover over the name: the property's full IRI.
function Line({ line, rdf }) {
  // For values from the source the marker is a link:
  // hovering shows the source's name, clicking jumps to the source's panel
  let marker
  if (line.where === 'source') {
    const label = oneText(rdf.store, rdf.source, RDFS_LABEL)
    marker = (
      <a href="#source-panel" className="marker source" title={'Source: ' + (label !== '' ? label : shortName(rdf.source, rdf.prefixes))}>
        {MARKERS.source}
      </a>
    )
  } else {
    marker = <span className={'marker ' + line.where}>{MARKERS[line.where]}</span>
  }

  return (
    <tr>
      <th title={line.property}>{line.name}</th>
      <td>
        <Value value={line.value} />
      </td>
      <td className="marker-cell">{marker}</td>
    </tr>
  )
}

// One value: readable text, the exact RDF when you hover, and a link if there is a web page for it
function Value({ value }) {
  if (value === null) return <span className="muted">—</span>

  return (
    <>
      <span title={value.hover}>{value.text}</span>
      {value.link && (
        <a className="vocab-link" href={value.link} target="_blank" rel="noreferrer" title={value.hover}>
          ↗ {value.linkName}
        </a>
      )}
    </>
  )
}

// The source's own panel: what it states (true for every field), and all its fields
function SourcePanel({ endpoint, rdf, fields, field, onPick }) {
  const label = oneText(rdf.store, rdf.source, RDFS_LABEL)

  return (
    <section className="card" id="source-panel">
      <h3>Source: {label !== '' ? label : endpoint.id}</h3>
      <p className="muted small">
        <span title={rdf.source.value}>{shortName(rdf.source, rdf.prefixes)}</span>. What the source states holds for every
        one of its fields.
      </p>
      <table className="facts">
        <tbody>
          {sourceLines(rdf).map((line, index) => (
            <tr key={index}>
              <th title={line.property}>{line.name}</th>
              <td>
                <Value value={line.value} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h4>Fields ({fields.length})</h4>
      <ul className="members">
        {fields.map((other) => {
          const role = roleText(rdf.store, rdf.source, other, rdf.prefixes)
          return (
            <li key={other.value}>
              {other.equals(field) ? (
                <strong>{fieldName(rdf.store, other, rdf.prefixes)} (shown above)</strong>
              ) : (
                <FieldLinks rdf={rdf} fields={fields} links={[other]} onPick={onPick} />
              )}
              {role !== '—' && <span className="muted small"> ({role})</span>}
            </li>
          )
        })}
      </ul>
    </section>
  )
}

// --- What the panels show, read from the store ----------------------------------

// The sections of the field panel (the table in sections.js), each with its lines, plus
// "Also stated" with everything else the field says, so nothing is hidden.
// (For embedded data, the "readings" line says how many points App.jsx read: rdf.points.)
function fieldSections(rdf, field) {
  const sections = []
  // The properties of the field that a line already shows; "Also stated" shows the rest.
  // slice() makes a copy, so adding to it does not change SHOWN_ELSEWHERE itself.
  const shown = SHOWN_ELSEWHERE.slice()

  for (const section of FIELD_SECTIONS) {
    const lines = []
    for (const property of section.properties) {
      // Look for the value where the table says: on the field, or on its source
      const node = property.on === 'field' ? field : rdf.source
      lines.push(makeLine(rdf, property.name, property.iri, one(rdf.store, node, property.iri), property.on))
      if (property.on === 'field') shown.push(property.iri)
    }
    sections.push({ title: section.title, explanation: section.explanation, lines: lines })
  }

  addBindingLines(rdf, sections[0])

  const others = alsoStatedLines(rdf, field, shown)
  if (others.length > 0) {
    sections.push({ title: 'Also stated', explanation: 'Everything else the metadata says about this field.', lines: others })
  }
  return sections
}

// One line of a section: what the inspector calls it, the property (full IRI), where the
// value is stated ('field', 'source', or 'missing' when there is none), and the value to show
function makeLine(rdf, name, property, term, on) {
  return {
    name: name,
    property: property,
    where: term ? on : 'missing',
    value: term ? describeTerm(rdf, term) : null,
  }
}

// "Data binding" also depends on the kind of source: first where the values are (the
// data file, or the points embedded in the metadata), last the time column and the key
function addBindingLines(rdf, binding) {
  const store = rdf.store
  const source = rdf.source

  if (isEmbedded(store, source) === true) {
    binding.explanation =
      'The values are embedded in the metadata (tss:points). They are read as JSON-LD, with a context ' +
      'made from the fields, so this field is one key in each point.'
    const line = makeLine(rdf, 'readings', TSS_POINTS, one(store, source, TSS_POINTS), 'source')
    if (line.value) {
      // rdf.points: the points App.jsx read as RDF, { store, rows, error } (see readPoints in tss.js)
      line.value.text = pointsText(rdf.points)
      line.value.hover = rdf.points.error ? rdf.points.error : 'tss:points (in the Turtle of the source below)'
    }
    binding.lines.unshift(line) // unshift: put it first
  } else {
    const dataFile = one(store, source, TSS_DATA_FILE)
    const line = makeLine(rdf, 'data file', TSS_DATA_FILE, dataFile, 'source')
    // The IRI itself, as text: the backend does not serve the data folder, so it is no link
    if (dataFile) line.value.text = dataFile.value
    binding.lines.unshift(line)
  }

  // The time field and the key are fields: their column name says more than their IRI
  const timeField = one(store, source, TSS_TIME_FIELD)
  const timeLine = makeLine(rdf, 'time column', TSS_TIME_FIELD, timeField, 'source')
  if (timeField) timeLine.value.text = fieldName(store, timeField, rdf.prefixes)
  binding.lines.push(timeLine)

  // A key can be made of several fields: the line names all their columns
  const keys = all(store, source, TSS_KEY)
  const keyLine = makeLine(rdf, 'key', TSS_KEY, keys.length > 0 ? keys[0] : null, 'source')
  if (keys.length > 0) {
    const names = []
    for (const key of keys) {
      names.push(fieldName(store, key, rdf.prefixes))
    }
    keyLine.value.text = names.join(', ')
  }
  binding.lines.push(keyLine)
}

// The text of the "readings" line of embedded data
//   points: rdf.points, { store, rows, error }
function pointsText(points) {
  if (points.error) return "can't be read as JSON-LD"
  return points.rows.length === 1 ? '1 point' : points.rows.length + ' points'
}

// Every other statement about the field, one line per value, sorted by the property's full IRI
function alsoStatedLines(rdf, field, shown) {
  const lines = []
  for (const quad of rdf.store.getQuads(field, null, null, null)) {
    const property = quad.predicate.value
    if (shown.includes(property)) continue
    lines.push(makeLine(rdf, shortName(property, rdf.prefixes), property, quad.object, 'field'))
  }
  lines.sort((a, b) => a.property.localeCompare(b.property, 'en'))
  return lines
}

// How many lines are stated on the field, on its source, or not at all
function countLines(sections) {
  const counts = { field: 0, source: 0, missing: 0 }
  for (const section of sections) {
    for (const line of section.lines) {
      counts[line.where] += 1
    }
  }
  return counts
}

// What the source panel shows: everything the source states (except SOURCE_PANEL_SKIPS),
// one line per value, sorted by the name shown
function sourceLines(rdf) {
  const lines = []
  for (const quad of rdf.store.getQuads(rdf.source, null, null, null)) {
    const property = quad.predicate.value
    if (SOURCE_PANEL_SKIPS.includes(property)) continue

    const value = describeTerm(rdf, quad.object)
    if (property === TSS_TIME_FIELD || property === TSS_KEY) value.text = fieldName(rdf.store, quad.object, rdf.prefixes)
    lines.push({ name: shortName(property, rdf.prefixes), property: property, value: value })
  }
  lines.sort((a, b) => a.name.localeCompare(b.name, 'en'))
  return lines
}

// How the inspector shows one RDF term: readable text, the exact term when you hover
// over it, and a link when the IRI has a web page:  { text, hover, link, linkName }
function describeTerm(rdf, term) {
  // A literal: its text, and as hover the literal as Turtle writes it, with its datatype
  // or language, e.g. "2021-03-03T23:45:00Z"^^xsd:dateTime
  const value = { text: term.value, hover: termText(term, rdf.prefixes), link: null, linkName: null }

  if (term.termType === 'NamedNode') {
    // An IRI: its rdfs:label when the document gives one, else its short name
    // (e.g. ex:sensor-grootdok-ec20); as hover the full IRI
    const label = one(rdf.store, term, RDFS_LABEL)
    value.text = label ? label.value : termText(term, rdf.prefixes)
    value.hover = term.value
    for (const vocabulary of LINKABLE_VOCABULARIES) {
      if (term.value.startsWith(vocabulary.start)) {
        value.link = term.value
        value.linkName = vocabulary.name
      }
    }
  } else if (term.termType === 'Literal') {
    const time = dateTimeText(term)
    if (time !== null) value.text = time
  } else {
    // A blank node (a node without an IRI, e.g. [ a prov:Activity ]): its rdfs:label when it has one
    const label = one(rdf.store, term, RDFS_LABEL)
    value.text = label ? label.value : '[ ] (a blank node)'
  }
  return value
}

// An xsd:dateTime literal as '2021-03-03 23:45 UTC', or null for any other literal
// (also for a text that is no valid date-time: then it is shown as it is)
function dateTimeText(term) {
  // dateTimeValue (tss.js) checks the datatype and the form, and takes a time without a
  // timezone as UTC, as every tab and the backend do
  const time = dateTimeValue(term)
  if (time === null) return null

  // toISOString always writes UTC, e.g. '2021-03-03T23:45:00.000Z': keep the date and hh:mm
  const iso = time.toISOString()
  return iso.slice(0, 10) + ' ' + iso.slice(11, 16) + ' UTC'
}
