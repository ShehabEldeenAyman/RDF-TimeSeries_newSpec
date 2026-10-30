import { useState, useEffect } from 'react'
import {
  one, has, isEmbedded,
  TSS_FORMAT, TSS_PARQUET, TSS_DATA_FILE,
  TSS_UNIT, TSS_OBSERVED_PROPERTY, TSS_DESCRIBES_FIELD,
} from './tss'
import { fieldLabel, fieldName, isTimeField, unitText } from './fields'

// The SQL box: type a SQL query, DuckDB runs it on the data of this endpoint (one table with a
// column per field, plus the metadata tables) and gives the result. For a bundled endpoint DuckDB
// runs in the backend; for your own endpoint it runs in this browser tab (DuckDB-WASM). The api
// (dataApi.js) asks the right one. It tells us the tables and their columns; what each column
// means (its field, label, unit) we read from the metadata (rdf.store), by the field's IRI.
export function SqlBox({ rdf, api }) {
  const [schema, setSchema] = useState(null) // the tables, their columns, and example queries
  const [sql, setSql] = useState('')
  const [result, setResult] = useState(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState(null)

  // Where the data is and in which format: from the metadata
  const embedded = isEmbedded(rdf.store, rdf.source) === true
  const format = one(rdf.store, rdf.source, TSS_FORMAT)
  const isParquet = format !== null && format.value === TSS_PARQUET

  // When the tab opens: ask which tables there are, and start with the first example.
  // (For your own endpoint this starts DuckDB in the browser: a few seconds the first time.)
  useEffect(() => {
    let ignore = false // see App.jsx: throw away an answer that arrives too late

    api.sqlTables()
      .then((data) => {
        if (ignore) return
        setSchema(data)
        if (data.examples.length > 0) setSql(data.examples[0].sql)
      })
      .catch((err) => {
        if (!ignore) setError(err.message)
      })

    return () => {
      ignore = true
    }
  }, [api])

  function runQuery() {
    setRunning(true)
    setResult(null)
    setError(null)

    api.sql(sql)
      .then((data) => setResult(data))
      .catch((err) => setError(err.message))
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

  if (schema === null && error === null) {
    return <p>{api.inBrowser ? 'Starting DuckDB in this browser tab and loading the data (a few seconds)…' : 'Loading…'}</p>
  }

  return (
    <div className="sql-layout">
      <div>
        <section className="card">
          <h3>SQL (DuckDB)</h3>
          <p className="muted small">
            Read-only SQL on {embedded ? 'the points embedded in the Turtle file' : 'the data file of this source'}: one table, with one column per field.
            {isParquet ? ' The columns keep the types of the Parquet file.' : ' Each column has the type of its field (tss:datatype).'}
            {' '}The <code>source</code> and <code>fields</code> tables hold the TSS metadata, so you can join it with the data.
            {api.inBrowser && ' DuckDB runs here, in this browser tab: the data and your queries are not sent anywhere.'}
            {schema && <> Only one SELECT, at most {schema.maxRows} rows back, stopped after {schema.maxSeconds} seconds.</>}
          </p>

          {schema && schema.examples.length > 0 && (
            <label className="sql-examples">
              Start from an example{' '}
              <select value="" onChange={(event) => pickExample(event.target.value)}>
                <option value="">-- pick an example --</option>
                {schema.examples.map((example, index) => (
                  <option key={index} value={index}>
                    {exampleText(rdf.store, example, rdf.prefixes)}
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

      {schema && <Tables schema={schema} rdf={rdf} />}
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
function Tables({ schema, rdf }) {
  return (
    <aside className="card sql-tables">
      <h3>Tables</h3>
      {schema.tables.map((table) => (
        <div key={table.name} className="sql-table">
          <h4>
            <code>{table.name}</code>
          </h4>
          <p className="muted small long-text">{dataText(rdf.store, rdf.source)}</p>
          {table.error && <p className="error small long-text">{table.error}</p>}
          <ul>
            {table.columns.map((column) => {
              const role = columnRole(rdf.store, rdf.source, column.field, rdf.prefixes)
              return (
                <li key={column.name}>
                  <code>{column.name}</code> <span className="muted small">{column.type}</span>
                  {/* hovering shows the full IRI of the field */}
                  {role && <div className="small sql-role" title={column.field ? column.field : undefined}>{role}</div>}
                </li>
              )
            })}
          </ul>
        </div>
      ))}

      {schema.metadataTables.map((table) => (
        <div key={table.name} className="sql-table">
          <h4>
            <code>{table.name}</code>
          </h4>
          <p className="muted small">{table.explanation}</p>
          <ul>
            {table.columns.map((column) => (
              <li key={column.name}>
                <code>{column.name}</code> <span className="muted small">{column.type}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </aside>
  )
}

// --- Reading the metadata -------------------------------------------------------

// The name of an example query in the list. The backend sends a fixed title, and the IRI of
// the field the example is about (or null); the field's label comes from the metadata, e.g.
// 'Daily mean, min and max: Electrical conductivity at 20 °C, ...'
function exampleText(store, example, prefixes) {
  // No field (null): the title alone. (Never look up a missing field: a null subject
  // would match every subject in the store.)
  if (!example.field) return example.title
  // Its rdfs:label, or its column name when it has no label (fieldLabel in fields.js)
  return example.title + ': ' + fieldLabel(store, example.field, prefixes)
}

// Where the data table comes from, in words: the embedded points or the data file
function dataText(store, source) {
  if (isEmbedded(store, source) === true) return 'the embedded points (tss:pointOf)'
  const dataFile = one(store, source, TSS_DATA_FILE)
  if (dataFile === null) return 'no tss:dataFile'
  return dataFile.value + ' (tss:dataFile)'
}

// What a column of the data table is, in words, e.g. "value of: Electrical conductivity ... (unit:MicroS-PER-CentiM)".
//   fieldIri: the IRI of the field with this column name (from the backend), or null
// Gives null when there is nothing to say.
function columnRole(store, source, fieldIri, prefixes) {
  if (fieldIri === null) return 'not described by a field'
  if (isTimeField(store, source, fieldIri)) return 'time (tss:timeField)'

  const described = one(store, fieldIri, TSS_DESCRIBES_FIELD)
  if (described !== null) return 'describes ' + fieldName(store, described, prefixes)

  if (has(store, fieldIri, TSS_OBSERVED_PROPERTY, null)) {
    const label = fieldLabel(store, fieldIri, prefixes) // its rdfs:label, or its column name
    const unit = one(store, fieldIri, TSS_UNIT)
    if (unit === null) return 'value of: ' + label
    // unitText (fields.js): the unit's rdfs:label, or its short name
    return 'value of: ' + label + ' (' + unitText(store, unit, prefixes) + ')'
  }
  return null
}
