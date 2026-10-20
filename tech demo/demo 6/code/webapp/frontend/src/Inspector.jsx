import { useState, useEffect } from 'react'
import { API_URL, errorText, fetchErrorText } from './config'
import { SourceFiles } from './SourceFiles'
import { roleText, firstField, fieldText } from './fields'

// How each "where" from the backend is shown next to a value
const MARKERS = {
  field: '● on the field',
  source: '⬆ from the source',
  missing: '○ not stated',
}

// The series inspector: pick a field (one column of the source) and see everything that is
// known about it, with, for every value, where it is stated: on the field, or on its source.
export function Inspector({ info }) {
  const [fieldId, setFieldId] = useState(firstField(info.fields))
  const [metadata, setMetadata] = useState(null)
  const [error, setError] = useState(null)

  // Every time another field is picked: ask the backend what is known about it
  useEffect(() => {
    if (fieldId === '') return

    // Same trick as in App: if another field is picked before this answer
    // arrives, the late answer is thrown away
    let ignore = false

    const params = new URLSearchParams({ field: fieldId })
    fetch(`${API_URL}/endpoints/${info.id}/series/metadata?${params}`)
      .then((response) => response.json())
      .then((data) => {
        if (ignore) return
        if (data.detail) setError(errorText(data.detail))
        else setMetadata(data)
      })
      .catch((err) => {
        if (!ignore) setError(fetchErrorText(err))
      })

    return () => {
      ignore = true
    }
  }, [info.id, fieldId])

  // Used by the drop down, and by the field links further down
  function pickField(id) {
    // Picking the field that is already shown: nothing to do. (Without this line
    // we would clear the panel, but the useEffect would not run again to refill it.)
    if (id === fieldId) return
    setFieldId(id)
    setMetadata(null)
    setError(null)
  }

  if (info.fields.length === 0) return <p className="muted">This data source has no fields.</p>

  return (
    <>
      <section className="card" id="inspector-top">
        <label>
          Field{' '}
          <select value={fieldId} onChange={(event) => pickField(event.target.value)}>
            {info.fields.map((field) => (
              <option key={field.id} value={field.id}>
                {fieldText(field)}
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

      {error && <p className="error long-text">Error: {error}</p>}
      {metadata === null && !error && <p>Loading…</p>}

      {metadata !== null && (
        <>
          <FieldPanel metadata={metadata} info={info} fieldId={fieldId} onPick={pickField} />
          <SourceFiles files={metadata.files} />
          <SourcePanel info={info} lines={metadata.source.lines} fieldId={fieldId} onPick={pickField} />
        </>
      )}
    </>
  )
}

function FieldPanel({ metadata, info, fieldId, onPick }) {
  const counts = metadata.counts
  const chosen = info.fields.find((field) => field.id === fieldId)

  // The field this one says something about, and the fields that say something about this one
  const describes = chosen.describesField ? info.fields.find((field) => field.columnName === chosen.describesField) : null
  const describedBy = info.fields.filter((field) => field.describesField === chosen.columnName && field.id !== chosen.id)

  return (
    <section className="card">
      <h3>{metadata.label}</h3>
      {metadata.comments.map((comment) => (
        <p key={comment} className="muted">{comment}</p>
      ))}
      <table className="facts">
        <tbody>
          <tr>
            <th>Field</th>
            <td title={metadata.field.iri}>{metadata.field.short}</td>
          </tr>
          <tr>
            <th>Source</th>
            <td>
              <a href="#source-panel">{info.source}</a> ({info.fields.length} fields)
            </td>
          </tr>
          {describes && (
            <tr>
              <th>Describes</th>
              <td><FieldLink field={describes} onPick={onPick} /></td>
            </tr>
          )}
          {describedBy.length > 0 && (
            <tr>
              <th>Described by</th>
              <td>
                {describedBy.map((field, index) => (
                  <span key={field.id}>
                    {index > 0 && ', '}
                    <FieldLink field={field} onPick={onPick} />
                  </span>
                ))}
              </td>
            </tr>
          )}
        </tbody>
      </table>

      <p className="summary">
        This field states <strong>{counts.field}</strong> of these values itself;{' '}
        <strong>{counts.source}</strong> come from its source
        {counts.missing > 0 && <>, and {counts.missing} are not stated</>}.
      </p>

      {metadata.sections.map((section) => (
        <div key={section.title} className="inspector-section">
          <h4>{section.title}</h4>
          <p className="muted small">{section.explanation}</p>
          <table className="inspector-lines">
            <tbody>
              {section.lines.map((line, index) => (
                <Line key={index} line={line} sourceLabel={info.label} />
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </section>
  )
}

// A link that shows another field in the inspector. href jumps back up to the field picker.
function FieldLink({ field, onPick }) {
  return (
    <a href="#inspector-top" onClick={() => onPick(field.id)}>
      {field.columnName}
    </a>
  )
}

// One line: name | value | where the value is stated
function Line({ line, sourceLabel }) {
  // For values from the source the marker is a link:
  // hovering shows the source's name, clicking jumps to the source's panel
  let marker
  if (line.where === 'source') {
    marker = (
      <a href="#source-panel" className="marker source" title={'Source: ' + sourceLabel}>
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
function SourcePanel({ info, lines, fieldId, onPick }) {
  return (
    <section className="card" id="source-panel">
      <h3>Source: {info.label}</h3>
      <p className="muted small">
        {info.source}. What the source states holds for every one of its fields.
      </p>
      <table className="facts">
        <tbody>
          {lines.map((line, index) => (
            <tr key={index}>
              <th>{line.property}</th>
              <td>
                <Value value={line.value} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h4>Fields ({info.fields.length})</h4>
      <ul className="members">
        {info.fields.map((field) => (
          <li key={field.id}>
            {field.id === fieldId ? <strong>{field.columnName} (shown above)</strong> : <FieldLink field={field} onPick={onPick} />}
            {roleText(field) !== '—' && <span className="muted small"> ({roleText(field)})</span>}
          </li>
        ))}
      </ul>
    </section>
  )
}
