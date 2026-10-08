import { useState, useEffect } from 'react'
import { API_URL, errorText, fetchErrorText } from './config'

// The SQL box: type a SQL query, the backend runs it with DuckDB on the CSV files
// of this endpoint (one table per CSV file) and sends the result back.
export function SqlBox({ info }) {
  const [schema, setSchema] = useState(null) // the tables, their columns, and example queries
  const [sql, setSql] = useState('')
  const [result, setResult] = useState(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState(null)

  // When the tab opens: ask which tables there are, and start with the first example
  useEffect(() => {
    let ignore = false // see App.jsx: throw away an answer that arrives too late

    fetch(`${API_URL}/endpoints/${info.id}/sql/tables`)
      .then((response) => response.json())
      .then((data) => {
        if (ignore) return
        if (data.detail) {
          setError(errorText(data.detail))
        } else {
          setSchema(data)
          if (data.examples.length > 0) setSql(data.examples[0].sql)
        }
      })
      .catch((err) => {
        if (!ignore) setError(fetchErrorText(err))
      })

    return () => {
      ignore = true
    }
  }, [info.id])

  function runQuery() {
    setRunning(true)
    setResult(null)
    setError(null)

    // POST, with the query in the body as JSON (a query can be too long for the address)
    fetch(`${API_URL}/endpoints/${info.id}/sql`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sql: sql }),
    })
      .then((response) => response.json())
      .then((data) => {
        if (data.detail) setError(errorText(data.detail))
        else setResult(data)
      })
      .catch((err) => setError(fetchErrorText(err)))
      .finally(() => setRunning(false))
  }

  // Ctrl+Enter (Cmd+Enter on a Mac) in the text box runs the query
  function handleKeyDown(event) {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault()
      runQuery()
    }
  }

  function pickExample(index) {
    if (index === '') return
    setSql(schema.examples[Number(index)].sql)
  }

  if (schema === null && error === null) return <p>Loading…</p>

  return (
    <div className="sql-layout">
      <div>
        <section className="card">
          <h3>SQL (DuckDB)</h3>
          <p className="muted small">
            Read-only SQL on the CSV files of this data source: one table per CSV file, with the column names and types
            from its CSVW file, plus a <code>snippets</code> table with the TSS2 metadata.
            {schema && <> Only one SELECT, at most {schema.maxRows} rows back, stopped after {schema.maxSeconds} seconds.</>}
          </p>

          {schema && schema.examples.length > 0 && (
            <label className="sql-examples">
              Start from an example{' '}
              <select value="" onChange={(event) => pickExample(event.target.value)}>
                <option value="">-- pick an example --</option>
                {schema.examples.map((example, index) => (
                  <option key={index} value={index}>
                    {example.title}
                  </option>
                ))}
              </select>
            </label>
          )}

          <textarea
            className="sql-text"
            rows={10}
            spellCheck={false}
            value={sql}
            onChange={(event) => setSql(event.target.value)}
            onKeyDown={handleKeyDown}
          />
          <p>
            <button onClick={runQuery} disabled={running || sql.trim() === ''}>
              {running ? 'Running…' : 'Run'}
            </button>{' '}
            <span className="muted small">or Ctrl+Enter</span>
          </p>
        </section>

        {/* DuckDB's error messages say what is wrong (and where), so show them as they are */}
        {error && <pre className="sql-error">{error}</pre>}
        {result && <SqlResult result={result} />}
      </div>

      {schema && <Tables schema={schema} />}
    </div>
  )
}

// The answer: a table with whatever columns the query asked for
function SqlResult({ result }) {
  return (
    <section className="card">
      <h3>
        {result.rows.length} {result.rows.length === 1 ? 'row' : 'rows'}
        {result.moreRows && ' (there are more: add a LIMIT, or a WHERE, to see the ones you want)'}
      </h3>
      <p className="muted small">Ran in {result.seconds} s</p>
      {result.skipped.length > 0 && (
        <p className="error small long-text">
          Not loaded (so not in this query):{' '}
          {result.skipped.map((table) => `${table.name}: ${table.error}`).join('; ')}
        </p>
      )}
      <div className="table-scroll">
        <table className="data">
          <thead>
            <tr>
              {result.columns.map((column, index) => (
                <th key={index}>{column}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {result.rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {row.map((value, valueIndex) => (
                  <td key={valueIndex}>{value === null ? <span className="muted">NULL</span> : String(value)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

// The tables you can query, with their columns: a cheat sheet next to the text box
function Tables({ schema }) {
  return (
    <aside className="card sql-tables">
      <h3>Tables</h3>
      {schema.tables.map((table) => (
        <div key={table.name} className="sql-table">
          <h4>
            <code>{table.name}</code>
          </h4>
          <p className="muted small long-text">
            {table.file} ({table.rdf})
          </p>
          {table.error && <p className="error small long-text">{table.error}</p>}
          <ul>
            {table.columns.map((column) => (
              <li key={column.name}>
                <code>{column.name}</code> <span className="muted small">{column.type}</span>
                {column.role && <div className="small sql-role">{column.role}</div>}
              </li>
            ))}
          </ul>
        </div>
      ))}

      <div className="sql-table">
        <h4>
          <code>snippets</code>
        </h4>
        <p className="muted small">The TSS2 metadata: one row per series.</p>
        <ul>
          {schema.snippetColumns.map((column) => (
            <li key={column.name}>
              <code>{column.name}</code> <span className="muted small">{column.type}</span>
            </li>
          ))}
        </ul>
      </div>
    </aside>
  )
}
