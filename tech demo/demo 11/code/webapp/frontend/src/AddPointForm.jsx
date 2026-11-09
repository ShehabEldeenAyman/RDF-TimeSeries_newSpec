import { useState } from 'react'
import { one, shortName, TSS_FROM, TSS_UNTIL, TSS_MODIFIED_AT, TSS_COMMITTED_UNTIL } from './tss'
import { nowText, pointFields, pointStatement } from './appendPoints'
import { AppendResult } from './AppendExample'
import { UpdateMetadata } from './UpdateMetadata'

// The "Add data" tab: a form to add one point (a time, and a value for each other field) to an
// embedded endpoint. The form writes the point as one Turtle statement (shown under it, while you
// type), and "Append the point" appends that statement to the endpoint's document, here in the
// browser (see appendPoints.js): a bundled endpoint gets a copy in this tab, and its file on the
// server never changes. App.jsx shows this tab only when canAppend says yes (tss:embedded true,
// tss:mutable true, a Turtle document). The "Append example" tab (AppendExample.jsx) appends a
// pre-made reading the same way.
//
// Under it, "Update the metadata" (UpdateMetadata.jsx) recalculates what an append leaves as it was.
//
// Props:
//   rdf:        the endpoint's metadata, as every tab gets it (App.jsx)
//   lastChange: the answer of the last append or update of this endpoint, or null
//               ({ kind: 'append', ... }: see AppendResult; { kind: 'update', ... }: see UpdateMetadata)
//   onAppend:   called with the Turtle text to append; gives a Promise (App.jsx makes the copy and picks it)
//   onUpdate:   called by "Update the metadata"; gives a Promise (see UpdateMetadata.jsx)
export function AddPointForm({ rdf, lastChange, onAppend, onUpdate }) {
  const fields = pointFields(rdf)
  // The text in each input, by field IRI. useState(function): made once, when the tab is drawn.
  const [texts, setTexts] = useState(() => emptyTexts(fields))
  const [sending, setSending] = useState(false)
  const [error, setError] = useState(null)

  // Each field's input changes only its own text
  function changeText(fieldIri, text) {
    setTexts(Object.assign({}, texts, { [fieldIri]: text }))
  }

  // The "now" button: the current time in UTC, to the second, e.g. 2026-10-26T09:30:12Z
  function fillNow(fieldIri) {
    changeText(fieldIri, nowText())
  }

  // The point's values, by field IRI: the time without spaces around it. An empty input means
  // "no value": the statement gets no value for that field.
  const values = {}
  for (const field of fields) {
    values[field.iri] = field.isTime ? texts[field.iri].trim() : texts[field.iri]
  }
  const statement = pointStatement(rdf, values)

  function submit(event) {
    event.preventDefault() // a form would load the page again: we append the point ourselves
    setSending(true)
    setError(null)
    // On success App.jsx picks the appended copy, and this tab is drawn anew (with "appended")
    onAppend(statement)
      .catch((err) => setError(err.message))
      .finally(() => setSending(false))
  }

  const points = rdf.points

  return (
    <>
      <section className="card">
        <h3>Add a point</h3>
        <p className="small">
          Fill in a time and the values, and the form writes the point as one Turtle statement (below), linked to the
          source with <code>tss:pointOf</code>. "Append the point" appends that statement after the last line of the
          metadata document, in this browser tab only: a bundled endpoint gets a copy (listed in the drop down as
          "(appended)"), and its file on the server never changes. The SHACL tab then checks the new point.
        </p>
        <p className="small">
          {windowText(rdf)}.{' '}
          {points && !points.error && <>{points.rows.length} points.</>}
          {points && points.error && <span className="error">The points can't be read: {points.error}</span>}
        </p>

        <form className="point-form" onSubmit={submit}>
          {fields.map((field) => (
            <FieldInput
              key={field.iri}
              field={field}
              text={texts[field.iri]}
              prefixes={rdf.prefixes}
              disabled={sending}
              onChange={(text) => changeText(field.iri, text)}
              onNow={() => fillNow(field.iri)}
            />
          ))}
          <div>
            <h4>The statement to append</h4>
            <pre>{statement}</pre>
            <button type="submit" disabled={sending}>
              {sending ? 'Appending…' : 'Append the point'}
            </button>
          </div>
        </form>

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

// One input of the form: the field's label, column name and datatype, and a box for its value
//   field: one entry of pointFields;  text: what the box holds;  onNow: the "now" button (time only)
function FieldInput({ field, text, prefixes, disabled, onChange, onNow }) {
  const inputId = 'point-' + field.iri
  return (
    <div className="point-field">
      <label htmlFor={inputId}>
        <strong>{field.label}</strong>{' '}
        <span className="muted small">
          {field.columnName !== '' && <>column <code>{field.columnName}</code>, </>}
          {field.datatype && <code>{shortName(field.datatype, prefixes)}</code>}
          {field.isTime && ', the time (needed)'}
        </span>
      </label>
      <div className="point-input">
        {/* A number field gets a number box (step="any": any decimals); the time and any other
            field a text box. The time is typed as an xsd:dateTime, like the points. */}
        <input
          id={inputId}
          type={field.numeric ? 'number' : 'text'}
          step={field.numeric ? 'any' : undefined}
          placeholder={field.isTime ? '2021-03-04T01:15:00Z' : ''}
          value={text}
          disabled={disabled}
          required={field.isTime}
          onChange={(event) => onChange(event.target.value)}
        />
        {field.isTime && (
          <button type="button" className="secondary" disabled={disabled} onClick={onNow}>
            now
          </button>
        )}
      </div>
    </div>
  )
}

// --- Helpers -----------------------------------------------------------------------

// An empty text for every field: { '<field IRI>': '' }
function emptyTexts(fields) {
  const texts = {}
  for (const field of fields) texts[field.iri] = ''
  return texts
}

// The source's window as text, e.g. Window [2021-03-03T23:15:00Z, 2026-01-01T00:00:00Z) (from
// included, until not), and when its metadata was last edited and checked
function windowText(rdf) {
  const from = one(rdf.store, rdf.source, TSS_FROM)
  const until = one(rdf.store, rdf.source, TSS_UNTIL)
  const modified = one(rdf.store, rdf.source, TSS_MODIFIED_AT)
  const committed = one(rdf.store, rdf.source, TSS_COMMITTED_UNTIL)
  let text = 'Window [' + (from ? from.value : 'open') + ', ' + (until ? until.value : 'open') + ')'
  if (modified) text += ', metadata last edited ' + modified.value + ' (tss:modifiedAt)'
  if (committed) text += ', checked up to date ' + committed.value + ' (tss:committedUntil)'
  return text
}
