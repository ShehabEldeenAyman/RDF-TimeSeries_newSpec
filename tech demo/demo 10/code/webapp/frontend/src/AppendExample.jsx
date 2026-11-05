import { useState } from 'react'
import { exampleValues, millisecondsText, pointFields, pointStatement } from './appendPoints'
import { UpdateMetadata } from './UpdateMetadata'

// The "Append example" tab: a pre-made new reading for this endpoint, written as the Turtle
// statement that is appended to its document, in a box you can change. "Append" appends it here
// in the browser (see appendPoints.js): a bundled endpoint gets a copy in this tab, and the file on
// the server never changes. Then every tab shows the copy: the Overview and the Files tab count one
// more point, the SHACL tab checks it, the Query tab finds the new reading.
// App.jsx shows this tab only when canAppend says yes (tss:embedded true, tss:mutable true, Turtle).
// Under it, "Update the metadata" (UpdateMetadata.jsx) recalculates what an append leaves as it was.
//
// Props:
//   rdf:        the endpoint's metadata, as every tab gets it (App.jsx)
//   lastChange: the answer of the last append or update of this endpoint, or null
//               ({ kind: 'append', ... }: see AppendResult; { kind: 'update', ... }: see UpdateMetadata)
//   onAppend:   called with the Turtle text to append; gives a Promise (App.jsx makes the copy and picks it)
//   onUpdate:   called by "Update the metadata"; gives a Promise (see UpdateMetadata.jsx)
export function AppendExample({ rdf, lastChange, onAppend, onUpdate }) {
  // useState(function): the example is made once, when the tab is drawn
  const [text, setText] = useState(() => exampleText(rdf))
  const [sending, setSending] = useState(false)
  const [error, setError] = useState(null)

  function append() {
    setSending(true)
    setError(null)
    // On success App.jsx picks the appended copy, and this tab is drawn anew (with "appended")
    onAppend(text)
      .catch((err) => setError(err.message))
      .finally(() => setSending(false))
  }

  function reset() {
    setText(exampleText(rdf))
    setError(null)
  }

  return (
    <>
      <section className="card">
        <h3>Append a new reading</h3>
        <p className="small">
          The points of this source are RDF at the end of its metadata document, one Turtle statement each, linked
          to the source with <code>tss:pointOf</code>. So a new reading is one more statement after the last line:
          nothing before it changes. Below is a pre-made one: one step after the newest point, with that point's
          values. Change it if you like (a value above the field's maximum, say, and see what the SHACL tab says).
        </p>
        <p className="muted small">
          This is a demo: the text is appended in this browser tab only. A bundled endpoint gets a copy, listed in the
          drop down as "(appended)"; its file on the server never changes. A reload of the page forgets the copy.
        </p>

        <h4>The document ends with</h4>
        <pre className="append-tail">{lastLines(rdf.text, 6)}</pre>

        <h4>Text to append</h4>
        <textarea
          className="append-text"
          aria-label="Text to append"
          rows={Math.max(6, text.split('\n').length + 1)}
          spellCheck={false}
          value={text}
          disabled={sending}
          onChange={(event) => setText(event.target.value)}
        />
        <div className="append-buttons">
          <button onClick={append} disabled={sending || text.trim() === ''}>
            {sending ? 'Appending…' : 'Append to this endpoint'}
          </button>
          <button className="secondary" onClick={reset} disabled={sending}>
            Reset the example
          </button>
        </div>

        {error && (
          <p className="error long-text" role="alert">
            Not appended: {error}
          </p>
        )}
        {lastChange && lastChange.kind === 'append' && <AppendResult answer={lastChange} />}
      </section>
      <UpdateMetadata rdf={rdf} updated={lastChange && lastChange.kind === 'update' ? lastChange : null} onUpdate={onUpdate} />
    </>
  )
}

// What an append did (both tabs show it after App.jsx picked the appended document):
//   answer: { before, after, text, copied, milliseconds }: the number of points before and after,
//           the appended text, whether a bundled endpoint was copied into this tab, and how long the
//           append took
export function AppendResult({ answer }) {
  return (
    <div className="result" aria-live="polite">
      <p>
        <span className="badge ok">Appended</span>{' '}
        {answer.copied
          ? 'This is a copy of the bundled endpoint, made in this browser tab, with the text appended'
          : 'The text was appended in this browser tab'}
        : {answer.before} {answer.before === 1 ? 'point' : 'points'} before, {answer.after} now. The file on the
        server is unchanged.
      </p>
      <p>
        Appended in <strong>{millisecondsText(answer.milliseconds)}</strong>{' '}
        <span className="muted small">(the text added after the last line, and the document read again and checked)</span>
      </p>
      <p className="muted small">
        An append only adds lines at the end of the document, so <code>tss:modifiedAt</code>,{' '}
        <code>tss:committedUntil</code>, the window (<code>tss:from</code>, <code>tss:until</code>) and the stored
        summaries say what they said before (the Query tab's "Check the stored summary" shows the difference):
        "Update the metadata" below recalculates them. The SHACL tab checks the new point.
      </p>
      <h4>The appended text</h4>
      <pre>{answer.text}</pre>
    </div>
  )
}

// The pre-made example: a comment line and the statement of one new point (see exampleValues)
function exampleText(rdf) {
  const values = exampleValues(rdf, pointFields(rdf))
  return '# A new reading, appended in this browser tab\n' + pointStatement(rdf, values)
}

// The last lines of a text (without the empty ones at its end)
function lastLines(text, count) {
  const lines = text.replace(/\s+$/, '').split('\n')
  return lines.slice(Math.max(0, lines.length - count)).join('\n')
}
