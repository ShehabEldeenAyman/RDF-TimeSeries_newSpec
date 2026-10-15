import { useState } from 'react'
import { API_URL, errorText, fetchErrorText } from './config'

export function Validation({ endpointId }) {
  const [result, setResult] = useState(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState(null)

  // Runs when the button is clicked, not when the tab opens:
  // validating a big file takes several seconds.
  function runValidation() {
    setRunning(true)
    setResult(null)
    setError(null)

    fetch(`${API_URL}/endpoints/${endpointId}/validate`)
      .then((response) => response.json())
      .then((data) => {
        if (data.detail) setError(errorText(data.detail))
        else setResult(data)
      })
      .catch((err) => setError(fetchErrorText(err)))
      .finally(() => setRunning(false)) // runs after a success and after an error
  }

  return (
    <section className="card">
      <h3>CSVW validation</h3>
      <p>
        The <code>csvw</code> Python library reads the CSV through its CSVW metadata
        (the <code>tss:describedby</code> file) and checks every row against the table schema:
        headers, datatypes and required values. Large files can take 10 to 20 seconds.
      </p>

      <button onClick={runValidation} disabled={running}>
        {running ? 'Validating…' : 'Run validation'}
      </button>

      {error && <p className="error long-text">Error: {error}</p>}
      {result && <ValidationResult result={result} />}
    </section>
  )
}

function ValidationResult({ result }) {
  if (result.applicable === false) return <p className="muted">{result.note}</p>

  return (
    <div className="result">
      <p>
        <span className={result.valid ? 'badge ok' : 'badge bad'}>
          {result.valid ? '✔ Valid' : '✘ Not valid'}
        </span>{' '}
        <span className="muted small">checked in {result.seconds} s</span>
      </p>

      {result.messages.length > 0 && (
        <>
          <h4>Messages ({result.messageCount})</h4>
          {result.messageCount > result.messages.length && (
            <p className="muted small">Showing the first {result.messages.length}.</p>
          )}
          <ul className="messages">
            {result.messages.map((message, index) => (
              <li key={index}>{message}</li>
            ))}
          </ul>
        </>
      )}

      {result.columns.length > 0 && (
        <>
          <h4>Columns in the table schema</h4>
          <div className="table-scroll">
            <table className="data">
              <thead>
                <tr>
                  <th>Name (what TSS2 refers to)</th>
                  <th>CSV header</th>
                  <th>Datatype</th>
                </tr>
              </thead>
              <tbody>
                {result.columns.map((column) => (
                  <tr key={column.name}>
                    <td><code>{column.name}</code></td>
                    <td>{column.title}</td>
                    <td>{column.datatype}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
