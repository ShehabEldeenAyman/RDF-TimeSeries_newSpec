import { useState } from 'react'
import { API_URL, errorText, fetchErrorText } from './config'

export function QueryForm({ info }) {
  // What the user fills in. The form starts at the beginning of the first series:
  // "2021-03-03T23:45:00+00:00".slice(0, 16) gives "2021-03-03T23:45", the format a datetime-local input wants.
  const first = info.snippets.length > 0 ? info.snippets[0] : null
  const [snippetId, setSnippetId] = useState(first ? first.id : '')
  const [start, setStart] = useState(first && first.from ? first.from.slice(0, 16) : '')
  const [end, setEnd] = useState('')
  const [limit, setLimit] = useState(100)

  // The snippet that is picked right now. Its time window can differ from the
  // group's, if the snippet states its own tss:from or tss:until.
  const chosen = info.snippets.find((snippet) => snippet.id === snippetId)

  // The answer from the backend
  const [result, setResult] = useState(null)
  const [requestUrl, setRequestUrl] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  function handleSubmit(event) {
    event.preventDefault() // stop the browser from reloading the page

    // URLSearchParams builds "snippet=...&start=...&end=...&limit=..." and escapes special characters for us
    // Number(limit) turns what was typed (e.g. "1e3") into a plain number (1000)
    const params = new URLSearchParams({ snippet: snippetId, start: start, end: end, limit: Number(limit) })
    const url = `${API_URL}/endpoints/${info.id}/query?${params}`

    setRequestUrl(url)
    setLoading(true)
    setResult(null)
    setError(null)

    fetch(url)
      .then((response) => response.json())
      .then((data) => {
        if (data.detail) setError(errorText(data.detail))
        else setResult(data)
      })
      .catch((err) => setError(fetchErrorText(err)))
      .finally(() => setLoading(false))
  }

  return (
    <>
      <section className="card">
        <h3>Query a time series</h3>
        <p className="muted small">
          {chosen && <>This series runs from {chosen.from} until {chosen.until} (until itself not included). </>}
          All times are UTC. Leave "Until" empty to read from "From" onwards.
        </p>

        <form className="query-form" onSubmit={handleSubmit}>
          <label>
            Time series (snippet)
            <select value={snippetId} onChange={(event) => setSnippetId(event.target.value)}>
              {info.snippets.map((snippet) => (
                <option key={snippet.id} value={snippet.id}>
                  {snippet.label}
                </option>
              ))}
            </select>
          </label>

          <label>
            From
            <input type="datetime-local" value={start} onChange={(event) => setStart(event.target.value)} />
          </label>

          <label>
            Until
            {/* min: the browser won't accept an "Until" before "From" */}
            <input type="datetime-local" min={start} value={end} onChange={(event) => setEnd(event.target.value)} />
          </label>

          <label>
            Max rows
            <input
              type="number"
              min="1"
              max="5000"
              required
              value={limit}
              onChange={(event) => setLimit(event.target.value)}
            />
          </label>

          <button type="submit" disabled={loading}>
            {loading ? 'Running…' : 'Run query'}
          </button>
        </form>
      </section>

      {error && <p className="error long-text">Error: {error}</p>}
      {result && <QueryResult result={result} requestUrl={requestUrl} />}
    </>
  )
}

function QueryResult({ result, requestUrl }) {
  return (
    <section className="card">
      <h3>
        {result.readings.length} readings
        {result.limitReached && ' (max rows reached, there may be more)'}
      </h3>

      <p className="muted small long-text">
        Request sent: <a href={requestUrl} target="_blank" rel="noreferrer">{requestUrl}</a>
      </p>
      <p className="muted small">
        How the backend answered: it opened <code>{result.csvFile}</code> (delimiter <code>{result.delimiter}</code>),
        took the time from column <code>{result.timeColumn}</code> (CSV header "{result.timeHeader}")
        and the value from column <code>{result.valueColumn}</code> (CSV header "{result.valueHeader}").
      </p>

      <div className="table-scroll">
        <table className="data">
          <thead>
            <tr>
              <th>Time</th>
              <th>Value ({result.unit ? result.unit : 'unit unknown'})</th>
            </tr>
          </thead>
          <tbody>
            {result.readings.map((reading) => (
              <tr key={reading.time}>
                <td>{reading.time}</td>
                <td>{reading.value === null ? '—' : reading.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
