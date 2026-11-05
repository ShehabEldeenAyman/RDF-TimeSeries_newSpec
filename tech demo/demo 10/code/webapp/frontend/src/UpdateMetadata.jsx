import { useState } from 'react'
import { one, readFields, readSummary, TSS_FROM, TSS_UNTIL, TSS_MODIFIED_AT, TSS_COMMITTED_UNTIL } from './tss'
import { millisecondsText } from './appendPoints'

// The "Update the metadata" card, under the Add data and Append example tabs: a button that
// recalculates, from the points, what an append leaves as it was: the window (tss:from,
// tss:until), tss:modifiedAt, tss:committedUntil and the stored summaries (see metadataUpdate.js).
// Like the appends, it runs in this browser tab only: a bundled endpoint gets a copy, and the
// file on the server never changes. App.jsx picks the updated document, so every tab shows it,
// and this card is drawn again with the answer (how long it took, and what changed).
//
// Props:
//   rdf:      the endpoint's metadata, as every tab gets it (App.jsx)
//   updated:  the answer of the last update of this endpoint, or null (see UpdateResult)
//   onUpdate: called when the button is pressed; gives a Promise (App.jsx makes the copy and picks it)
export function UpdateMetadata({ rdf, updated, onUpdate }) {
  const [running, setRunning] = useState(false)
  const [error, setError] = useState(null)

  function update() {
    setRunning(true)
    setError(null)
    // On success App.jsx picks the updated document, and this card is drawn anew (with "updated")
    onUpdate()
      .catch((err) => setError(err.message))
      .finally(() => setRunning(false))
  }

  const summaries = readFields(rdf.store, rdf.source).filter((field) => readSummary(rdf.store, field) !== null).length

  return (
    <section className="card">
      <h3>Update the metadata</h3>
      <p className="small">
        An append only adds lines at the end of the document, so what the document states earlier stays as it was.
        This button recalculates it from the points:
      </p>
      <ul className="small">
        <li>
          the window: <code>tss:until</code> becomes the newest point plus one second (<code>tss:until</code> is not in
          the window) when that is later, and <code>tss:from</code> the oldest point when that is earlier;
        </li>
        <li>
          <code>tss:modifiedAt</code> and <code>tss:committedUntil</code>: now;
        </li>
        <li>
          the stored summaries ({summaries}): their window, <code>prov:generatedAtTime</code>, and the numbers each one
          states (count, missing, first and last time, and for numbers the minimum, maximum, mean and median).
        </li>
      </ul>
      <p className="muted small">Now: {currentText(rdf)}.</p>
      <p className="muted small">
        In this browser tab only, like the appends: a bundled endpoint gets a copy, and the file on the server never
        changes. The document is written again as Turtle: the same triples, but not its comments.
      </p>
      <button onClick={update} disabled={running}>
        {running ? 'Updating…' : 'Update the metadata'}
      </button>
      {error && (
        <p className="error long-text" role="alert">
          Not updated: {error}
        </p>
      )}
      {updated && <UpdateResult answer={updated} />}
    </section>
  )
}

// What an update did: how long each step took, and every value that changed
//   answer: { changes, timings, summaries, copied } (see handleUpdate in App.jsx and metadataUpdate.js)
function UpdateResult({ answer }) {
  const timings = answer.timings
  return (
    <div className="result" aria-live="polite">
      <p>
        <span className="badge ok">Updated</span>{' '}
        {answer.copied
          ? 'This is a copy of the bundled endpoint, made in this browser tab, with its metadata updated'
          : 'The metadata was updated in this browser tab'}
        . Calculated in <strong>{millisecondsText(timings.window + timings.summaries)}</strong>. The file on the server
        is unchanged.
      </p>
      <table className="data timings">
        <tbody>
          <tr>
            <th>The window, tss:modifiedAt and tss:committedUntil</th>
            <td>{millisecondsText(timings.window)}</td>
          </tr>
          <tr>
            <th>The stored summaries ({answer.summaries})</th>
            <td>{millisecondsText(timings.summaries)}</td>
          </tr>
          <tr>
            <th>Writing the document as Turtle, and reading it back</th>
            <td>{millisecondsText(timings.writing)}</td>
          </tr>
          <tr>
            <th>Total</th>
            <td><strong>{millisecondsText(timings.total)}</strong></td>
          </tr>
        </tbody>
      </table>

      <h4>What changed ({answer.changes.length})</h4>
      {answer.changes.length === 0 ? (
        <p className="muted small">Nothing: every value was already up to date.</p>
      ) : (
        <div className="table-scroll">
          <table className="data">
            <thead>
              <tr>
                <th>Of</th>
                <th>Property</th>
                <th>Before</th>
                <th>Now</th>
              </tr>
            </thead>
            <tbody>
              {answer.changes.map((change, index) => (
                <tr key={index}>
                  <td>{change.subject}</td>
                  <td><code>{change.property}</code></td>
                  <td className="long-text">{change.before !== null ? change.before : <span className="muted">not stated</span>}</td>
                  <td className="long-text">{change.after !== null ? change.after : <span className="muted">not stated</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// The source's window and times as they are now, e.g.
// window [2021-03-03T23:15:00Z, 2026-01-01T00:00:00Z), tss:modifiedAt ..., tss:committedUntil ...
function currentText(rdf) {
  const from = one(rdf.store, rdf.source, TSS_FROM)
  const until = one(rdf.store, rdf.source, TSS_UNTIL)
  const modified = one(rdf.store, rdf.source, TSS_MODIFIED_AT)
  const committed = one(rdf.store, rdf.source, TSS_COMMITTED_UNTIL)
  let text = 'window [' + (from ? from.value : 'open') + ', ' + (until ? until.value : 'open') + ')'
  text += ', tss:modifiedAt ' + (modified ? modified.value : 'not stated')
  text += ', tss:committedUntil ' + (committed ? committed.value : 'not stated')
  return text
}
