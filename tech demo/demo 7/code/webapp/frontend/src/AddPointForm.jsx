import { useState } from 'react'
import { parseRdf } from './rdf'
import { ShaclReport } from './Shacl'
import { readReport, mergePrefixes } from './shaclReport'
import { fieldLabel } from './fields'
import {
  one, oneText, readFields, isNumericDatatype, shortName,
  TSS_COLUMN_NAME, TSS_DATATYPE, TSS_TIME_FIELD, TSS_FROM, TSS_UNTIL, TSS_MODIFIED_AT, TSS_COMMITTED_UNTIL,
} from './tss'

// The "Add data" tab: a form to add one point (a time, and a value for each other field) to the
// embedded points (tss:points) of a bundled endpoint. App.jsx shows this tab only for a bundled
// endpoint whose source says tss:embedded true and tss:mutable true.
//
// What happens when you click "Add the point" (see backend/points_routes.py):
//   1. the backend checks the values (a time with a timezone, the XSD form of each datatype, no
//      second point at the same time),
//   2. changes the metadata with SPARQL Update: tss:points (the old points plus the new one), the
//      source's window (tss:from, tss:until), tss:modifiedAt and tss:committedUntil (both become the
//      time of the write, not the time of the new point), and every stored summary,
//   3. checks the result with SHACL (the default shapes for embedded points), and writes the file
//      only when it conforms.
// After a save, App.jsx loads the endpoint again, so every tab shows the new data. That draws this
// tab again too: the answer of the save comes back as the "saved" prop, so it is still shown.
//
// Props:
//   rdf:     the endpoint's metadata, as every tab gets it (App.jsx)
//   api:     the endpoint's data questions (dataApi.js); api.addPoint sends the point
//   saved:   the answer of the last save of this endpoint, or null (see onSaved)
//   onSaved: called with that answer after a save: App.jsx keeps it and loads the endpoint again
export function AddPointForm({ rdf, api, saved, onSaved }) {
  const fields = pointFields(rdf.store, rdf.source, rdf.prefixes)
  // The text in each input, by field IRI. useState(function): made once, when the tab is drawn.
  const [texts, setTexts] = useState(() => emptyTexts(fields))
  const [sending, setSending] = useState(false)
  // The answer to show: the last save (it arrives as a prop after the reload), or a refusal by
  // SHACL (nothing was saved, so there is no reload and we keep it here)
  const [answer, setAnswer] = useState(saved)
  const [error, setError] = useState(null)
  const [savedNow, setSavedNow] = useState(false) // saved; the endpoint is being loaded again

  // Each field's input changes only its own text
  function changeText(fieldIri, text) {
    setTexts(Object.assign({}, texts, { [fieldIri]: text }))
  }

  // The "now" button: the current time in UTC, to the second, e.g. 2026-10-26T09:30:12Z
  // (toISOString gives 2026-10-26T09:30:12.345Z: the milliseconds are left out)
  function fillNow(fieldIri) {
    changeText(fieldIri, new Date().toISOString().slice(0, 19) + 'Z')
  }

  function submit(event) {
    event.preventDefault() // a form would load the page again: we send the point ourselves
    // The values to send, by field IRI. The time without spaces around it; an empty input
    // means "no value" (the point gets no key for that field).
    const values = {}
    for (const field of fields) {
      let text = texts[field.iri]
      if (field.isTime) text = text.trim()
      if (text !== '') values[field.iri] = text
    }
    setSending(true)
    setAnswer(null)
    setError(null)
    const started = performance.now()
    api.addPoint(values)
      // The report comes as Turtle: parse it, to show it like the SHACL tab does
      .then((result) => readAnswer(result, rdf, api.shaclBase, started))
      .then((result) => {
        if (result.conforms) {
          // Saved: App.jsx keeps the answer and loads the endpoint again (this tab is drawn anew)
          setSavedNow(true)
          onSaved(result)
        } else {
          setAnswer(result) // SHACL said no: nothing was saved
        }
      })
      .catch((err) => setError(err.message))
      .finally(() => setSending(false))
  }

  const windowNow = windowText(rdf)
  const points = rdf.points

  return (
    <section className="card">
      <h3>Add a point</h3>
      <p className="small">
        Adds one point to the embedded points (<code>tss:points</code>) of this endpoint's file. The backend
        changes the metadata with SPARQL Update (the points, the source's window,{' '}
        <code>tss:modifiedAt</code> and <code>tss:committedUntil</code> (both become the time of the write), and
        the stored summaries) and checks the result with SHACL. Only when it
        conforms is the file written; it is written again by rdflib, so its comments are lost.
      </p>
      <p className="small">
        Window now: {windowNow}.{' '}
        {points && !points.error && <>{points.rows.length} points.</>}
        {points && points.error && <span className="error">The points can't be read: {points.error}</span>}
      </p>
      <p className="muted small">
        A point outside the window makes the window bigger: <code>tss:from</code> moves back to it, or{' '}
        <code>tss:until</code> moves to one second after it (<code>tss:until</code> itself is not in the window).
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
          <button type="submit" disabled={sending || savedNow}>
            {sending ? 'Adding…' : 'Add the point'}
          </button>
        </div>
      </form>

      {savedNow && <p role="status">Saved. Loading the endpoint again…</p>}
      {error && (
        <p className="error long-text" role="alert">
          Not added: {error} Nothing was saved.
        </p>
      )}
      {answer && <PointAnswer answer={answer} rdf={rdf} />}
    </section>
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
          column <code>{field.columnName}</code>
          {field.datatype && <>, <code>{shortName(field.datatype, prefixes)}</code></>}
          {field.isTime && ', the time (needed)'}
        </span>
      </label>
      <div className="point-input">
        {/* A number field gets a number box (step="any": any decimals); the time and any other
            field a text box. The time is typed as an xsd:dateTime with a timezone, like the points. */}
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

// What the backend answered: saved (conforms) or refused by SHACL, with the SPARQL updates
// that ran and the SHACL report
function PointAnswer({ answer, rdf }) {
  return (
    <div className="result" aria-live="polite">
      {answer.conforms ? (
        <p>
          <span className="badge ok">Saved</span> The new point is in the file. SHACL: conforms. Now {answer.points}{' '}
          points, window [{answer.from ?? 'open'}, {answer.until ?? 'open'}), <code>tss:modifiedAt</code>{' '}
          {answer.modifiedAt}, <code>tss:committedUntil</code> {answer.committedUntil}. Every tab shows the new data.
        </p>
      ) : (
        <p>
          <span className="badge bad">Not saved</span> SHACL: does not conform, so <strong>nothing was saved</strong>:
          the file is unchanged. The violations are below.
        </p>
      )}

      <h4>SHACL report</h4>
      <ShaclReport report={answer.shacl} rdf={rdf} inBrowser={false} />

      <h4>The SPARQL updates that ran</h4>
      <p className="muted small">
        {answer.conforms
          ? 'They ran on the metadata graph (rdflib) before it was checked and written to the file.'
          : 'They ran on a copy of the metadata graph in memory; it was not written.'}{' '}
        The values come with initBindings, listed under each update (the tss:points literal shortened).
      </p>
      {answer.sparql.map((update, index) => (
        <SparqlUpdate key={index} update={update} />
      ))}
    </div>
  )
}

// One SPARQL Update: its text exactly as it ran, and the values given to its variables
function SparqlUpdate({ update }) {
  return (
    <div className="sparql-update">
      <pre className="sparql-text">{update.update}</pre>
      <table className="data">
        <tbody>
          {update.bindings.map((binding) => (
            <tr key={binding.variable}>
              <th>{binding.variable}</th>
              <td className="long-text">
                <span className="sparql-binding">{binding.value}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// --- Helpers -----------------------------------------------------------------------

// The fields a point can hold a value for (those with a tss:columnName), the time field first,
// each as { iri, label, columnName, datatype, numeric, isTime }
//   prefixes: the document's prefixes, for a field without a label (its short name)
function pointFields(store, source, prefixes) {
  const timeField = one(store, source, TSS_TIME_FIELD)
  const found = []
  for (const field of readFields(store, source)) {
    const columnName = oneText(store, field, TSS_COLUMN_NAME)
    if (columnName === '') continue
    const datatype = one(store, field, TSS_DATATYPE)
    const isTime = timeField !== null && field.value === timeField.value // IRIs compared in full
    found.push({
      iri: field.value,
      label: fieldLabel(store, field, prefixes),
      columnName: columnName,
      datatype: datatype,
      numeric: !isTime && isNumericDatatype(datatype),
      isTime: isTime,
    })
  }
  // The time field first, the others in the order of readFields
  found.sort((a, b) => (a.isTime === b.isTime ? 0 : a.isTime ? -1 : 1))
  return found
}

// An empty text for every field: { '<field IRI>': '' }
function emptyTexts(fields) {
  const texts = {}
  for (const field of fields) texts[field.iri] = ''
  return texts
}

// The source's window as text, e.g. [2021-03-03T23:15:00Z, 2026-01-01T00:00:00Z) (from included, until not)
function windowText(rdf) {
  const from = one(rdf.store, rdf.source, TSS_FROM)
  const until = one(rdf.store, rdf.source, TSS_UNTIL)
  const modified = one(rdf.store, rdf.source, TSS_MODIFIED_AT)
  const committed = one(rdf.store, rdf.source, TSS_COMMITTED_UNTIL)
  let text = '[' + (from ? from.value : 'open') + ', ' + (until ? until.value : 'open') + ')'
  if (modified) text += ', metadata last edited ' + modified.value + ' (tss:modifiedAt)'
  if (committed) text += ', data last written ' + committed.value + ' (tss:committedUntil)'
  return text
}

// The backend's answer, with its SHACL report (Turtle) parsed and read like the SHACL tab reads it
// (readReport). Gives a Promise of the answer plus "shacl", the report as ShaclReport wants it.
//   started: when the request was sent (performance.now()), to say how long it took
function readAnswer(result, rdf, baseIri, started) {
  return parseRdf(result.report, 'text/turtle', baseIri).then((parsed) => {
    const read = readReport(parsed.store)
    const shacl = {
      store: parsed.store,
      text: result.report,
      prefixes: mergePrefixes(rdf.prefixes, parsed.prefixes),
      conforms: read.conforms,
      shapesSource: read.shapesSource,
      results: read.results,
      counts: read.counts,
      shapesName: null, // the default shapes (the report names the file)
      seconds: ((performance.now() - started) / 1000).toFixed(2),
    }
    return Object.assign({}, result, { shacl: shacl })
  })
}
