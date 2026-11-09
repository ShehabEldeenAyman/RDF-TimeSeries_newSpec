import { useState } from 'react'
import { fieldLabel } from './fields'
import { SummaryValue } from './SummaryTable'
import { one, readSummary, numberValue, timeValue, isNumericDatatype, TSS_FROM, TSS_UNTIL, TSS_DATATYPE } from './tss'

// The query that puts a field's stored summary (tss:summary in the metadata) next to the same
// numbers computed from the data now, and says for each one whether it is still the same.
// The stored numbers come from the metadata (rdf.store); the api's query (dataApi.js: the backend's
// /query, or the same computed in this browser tab) computes the others.

// The numbers a summary and /query both have:
//   key:     the attribute of the summary (see SUMMARY_ATTRIBUTES in tss.js)
//   name:    for people
//   numeric: only for numeric fields
//   compare: how to compare the two ('number', 'time', or 'mean')
//   data:    where the number is in /query's summary
const CHECKS = [
  { key: 'count', name: 'values', numeric: false, compare: 'number', data: (summary) => summary.count },
  { key: 'missing', name: 'empty values', numeric: false, compare: 'number', data: (summary) => summary.empty },
  { key: 'firstTime', name: 'first time', numeric: false, compare: 'time', data: (summary) => summary.firstTime },
  { key: 'lastTime', name: 'last time', numeric: false, compare: 'time', data: (summary) => summary.lastTime },
  { key: 'minimum', name: 'observed minimum', numeric: true, compare: 'number', data: (summary) => (summary.min ? summary.min.value : null) },
  { key: 'maximum', name: 'observed maximum', numeric: true, compare: 'number', data: (summary) => (summary.max ? summary.max.value : null) },
  { key: 'mean', name: 'mean', numeric: true, compare: 'mean', data: (summary) => summary.mean },
  { key: 'median', name: 'median', numeric: true, compare: 'number', data: (summary) => summary.median },
]

// Is the stored value (a literal) the same as the one computed now? null: nothing to compare
// (the summary does not state it, or the data has no value).
function sameValue(stored, computed, compare) {
  if (stored === null || computed === null || computed === undefined) return null
  if (compare === 'time') {
    // Compare the moments, not the texts: ...Z and ...+00:00 are the same time
    const moment = timeValue(stored)
    return moment !== null && moment === Date.parse(computed)
  }
  const number = numberValue(stored)
  if (number === null) return false
  // The backend rounds the mean to 3 decimals
  if (compare === 'mean') return Math.abs(number - computed) <= 0.0005
  // Sums of many decimals can differ in their last digits (5531.94 vs 5531.9400000000005)
  return Math.abs(number - computed) <= 1e-9 * Math.max(1, Math.abs(number))
}

// Does the summary cover the same window as the source? Then /query needs no extra times:
// it reads [tss:from, tss:until) of the source anyway. (Compared as moments.)
function sameWindowAsSource(store, source, summary) {
  const from = summary.values.from
  const until = summary.values.until
  const sameFrom = from === null || timeValue(from) === timeValue(one(store, source, TSS_FROM))
  const sameUntil = until === null || timeValue(until) === timeValue(one(store, source, TSS_UNTIL))
  return sameFrom && sameUntil
}

// The query, for one field. QueryForm.jsx gives it key={fieldIri}, so picking another field
// starts it again with an empty answer.
//   rdf, api: as for every tab   fieldIri: the field picked in the query form
export function SummaryCheck({ rdf, api, fieldIri }) {
  const [answer, setAnswer] = useState(null) // the summary /query computed, or null
  const [note, setNote] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const summary = readSummary(rdf.store, fieldIri)
  const numeric = isNumericDatatype(one(rdf.store, fieldIri, TSS_DATATYPE))
  const label = fieldLabel(rdf.store, fieldIri, rdf.prefixes)

  function runCheck() {
    // No filters and no grouping: every reading of the field in the summary's window counts
    const params = new URLSearchParams({ field: fieldIri, limit: 1 })
    if (sameWindowAsSource(rdf.store, rdf.source, summary)) {
      setNote("The summary covers the source's own window [tss:from, tss:until), which /query reads anyway.")
    } else {
      if (summary.values.from) params.set('start', summary.values.from.value)
      if (summary.values.until) params.set('end', summary.values.until.value)
      setNote("The summary covers another window than the source: /query gets its from and until as start and end " +
        '(the query form includes the end itself, the summary does not).')
    }
    setLoading(true)
    setAnswer(null)
    setError(null)
    api.query(params)
      .then((data) => setAnswer(data.summary))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }

  if (summary === null) {
    return (
      <section className="card">
        <h3>Check the stored summary</h3>
        <p className="muted small">"{label}" has no stored summary (tss:summary) in the metadata.</p>
      </section>
    )
  }

  // The checks that apply to this field: the numbers only for a numeric field
  const checks = CHECKS.filter((check) => numeric || !check.numeric)
  let differences = 0
  if (answer !== null) {
    for (const check of checks) {
      if (sameValue(summary.values[check.key], check.data(answer), check.compare) === false) differences += 1
    }
  }

  return (
    <section className="card">
      <h3>Check the stored summary</h3>
      <p className="muted small">
        The metadata stores a summary of "{label}" (<code>tss:summary</code>), computed
        {summary.values.generatedAt ? <> at <SummaryValue term={summary.values.generatedAt} prefixes={rdf.prefixes} /></> : ' at an unknown time'}.
        Data can change after that, and the summary is not refreshed by itself. This query computes the same
        numbers from the data now and puts them side by side.
      </p>
      <button type="button" onClick={runCheck} disabled={loading}>
        {loading ? 'Computing…' : 'Compare with the data now'}
      </button>
      {error && <p className="error long-text">Error: {error}</p>}

      {answer !== null && (
        <>
          <p className="small">
            {differences === 0
              ? <><span className="badge ok">up to date</span> Every stated number of the summary matches the data.</>
              : <><span className="badge bad">out of date</span> {differences} of the numbers differ from the data now.</>}
          </p>
          <p className="muted small">{note}</p>
          <table className="data">
            <thead>
              <tr>
                <th></th>
                <th>Stored (tss:summary)</th>
                <th>From the data now</th>
                <th>Same?</th>
              </tr>
            </thead>
            <tbody>
              {checks.map((check) => {
                const stored = summary.values[check.key]
                const computed = check.data(answer)
                const same = sameValue(stored, computed, check.compare)
                return (
                  <tr key={check.key}>
                    <th>{check.name}</th>
                    <td><SummaryValue term={stored} prefixes={rdf.prefixes} /></td>
                    <td>{computed === null || computed === undefined ? '—' : String(computed)}</td>
                    <td>
                      {same === true && <span className="badge ok">same</span>}
                      {same === false && <span className="badge bad">differs</span>}
                      {same === null && <span className="muted small">not compared</span>}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </>
      )}
    </section>
  )
}
