import { useState, useEffect } from 'react'
import { API_URL, errorText, fetchErrorText } from './config'
import { SourceFiles } from './SourceFiles'

// How each "source" from the backend is shown next to a value
const MARKERS = {
  own: '● own',
  group: '⬆ from group',
  override: '◆ overrides group',
  missing: '○ not stated',
}

// The series inspector: pick a series and see everything that is known about it,
// with, for every value, where it comes from (the snippet itself or its group).
// The backend applies the TSS2 inheritance rule; this component only shows the result.
export function Inspector({ info }) {
  const [snippetId, setSnippetId] = useState(info.snippets.length > 0 ? info.snippets[0].id : '')
  const [metadata, setMetadata] = useState(null)
  const [error, setError] = useState(null)

  // Every time another series is picked: ask the backend what is known about it
  useEffect(() => {
    if (snippetId === '') return

    // Same trick as in App: if another series is picked before this answer
    // arrives, the late answer is thrown away
    let ignore = false

    const params = new URLSearchParams({ snippet: snippetId })
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
  }, [info.id, snippetId])

  // Used by the drop down, and by the member list in the group panel
  function pickSeries(id) {
    // Picking the series that is already shown: nothing to do. (Without this line
    // we would clear the panel, but the useEffect would not run again to refill it.)
    if (id === snippetId) return
    setSnippetId(id)
    setMetadata(null)
    setError(null)
  }

  if (info.snippets.length === 0) return <p className="muted">This data source has no snippets.</p>

  return (
    <>
      <section className="card" id="inspector-top">
        <label>
          Series{' '}
          <select value={snippetId} onChange={(event) => pickSeries(event.target.value)}>
            {info.snippets.map((snippet) => (
              <option key={snippet.id} value={snippet.id}>
                {snippet.label}
              </option>
            ))}
          </select>
        </label>
        <p className="muted small legend">
          <span className="marker own">{MARKERS.own}</span> stated on the snippet itself ·{' '}
          <span className="marker group">{MARKERS.group}</span> inherited from its group's template ·{' '}
          <span className="marker override">{MARKERS.override}</span> the snippet replaces the group's value.
          Hover over a value to see the exact RDF.
        </p>
      </section>

      {error && <p className="error long-text">Error: {error}</p>}
      {metadata === null && !error && <p>Loading…</p>}

      {metadata !== null && (
        <>
          <SeriesPanel metadata={metadata} />
          <SourceFiles files={metadata.files} />
          {metadata.group && <GroupPanel group={metadata.group} currentId={snippetId} onPick={pickSeries} />}
        </>
      )}
    </>
  )
}

function SeriesPanel({ metadata }) {
  const counts = metadata.counts
  const groupLabel = metadata.group ? metadata.group.label : ''

  return (
    <section className="card">
      <h3>{metadata.label}</h3>
      <table className="facts">
        <tbody>
          <tr>
            <th>Snippet</th>
            <td title={metadata.snippet.iri}>{metadata.snippet.short}</td>
          </tr>
          <tr>
            <th>Group</th>
            <td>
              {metadata.group ? (
                <>
                  <a href="#group-panel" title={metadata.group.value.hover}>{metadata.group.short}</a>{' '}
                  ({metadata.group.size} {metadata.group.size === 1 ? 'snippet' : 'snippets'})
                </>
              ) : (
                '— (this snippet is not in a group)'
              )}
            </td>
          </tr>
        </tbody>
      </table>

      <p className="summary">
        This snippet states <strong>{counts.own + counts.override}</strong> of these values itself
        and inherits <strong>{counts.group}</strong> from its group
        {counts.override > 0 && <> ({counts.override} of its own values override the group)</>}.
      </p>

      {metadata.sections.map((section) => (
        <div key={section.title} className="inspector-section">
          <h4>{section.title}</h4>
          <p className="muted small">{section.explanation}</p>
          <table className="inspector-lines">
            <tbody>
              {section.lines.map((line) => (
                <Line key={line.name} line={line} groupLabel={groupLabel} />
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </section>
  )
}

// One line: name | value | where the value comes from
function Line({ line, groupLabel }) {
  // For inherited and overriding values the marker is a link:
  // hovering shows the group's name, clicking jumps to the group's panel
  let marker
  if (line.source === 'group' || line.source === 'override') {
    marker = (
      <a href="#group-panel" className={'marker ' + line.source} title={'Group: ' + groupLabel}>
        {MARKERS[line.source]}
      </a>
    )
  } else {
    marker = <span className={'marker ' + line.source}>{MARKERS[line.source]}</span>
  }

  return (
    <tr>
      <th title={line.property}>{line.name}</th>
      <td>
        <Value value={line.value} />
        {line.groupValue && (
          <s className="group-value" title="The group's value, replaced by this snippet">
            <Value value={line.groupValue} />
          </s>
        )}
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

// The group's own panel: the defaults in its template, and all its members
function GroupPanel({ group, currentId, onPick }) {
  return (
    <section className="card" id="group-panel">
      <h3>Group: {group.label}</h3>
      <p className="muted small">
        <span title={group.value.hover}>{group.short}</span>. Its snippet template gives these defaults to every
        member. A member uses them unless it states its own value.
      </p>
      <table className="facts">
        <tbody>
          {group.template.map((line, index) => (
            <tr key={index}>
              <th>{line.property}</th>
              <td>
                <Value value={line.value} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h4>Members ({group.members.length})</h4>
      <ul className="members">
        {group.members.map((member) => (
          <li key={member.id}>
            {member.id === currentId ? (
              <strong>{member.label} (shown above)</strong>
            ) : (
              // href jumps back up to the series picker, onClick shows this member
              <a href="#inspector-top" onClick={() => onPick(member.id)}>
                {member.label}
              </a>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}
