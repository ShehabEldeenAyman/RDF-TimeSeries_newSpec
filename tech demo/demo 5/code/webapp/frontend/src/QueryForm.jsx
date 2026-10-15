import { useState, useEffect } from 'react'
import { API_URL, errorText, fetchErrorText } from './config'

// The words the form and the results use for the backend's options
const RESAMPLE_NAMES = { '': 'every reading', hour: 'one value per hour', day: 'one value per day' }

export function QueryForm({ info }) {
  // What the user fills in. The form starts at the beginning of the first series:
  // "2021-03-03T23:45:00+00:00".slice(0, 16) gives "2021-03-03T23:45", the format a datetime-local input wants.
  const first = info.snippets.length > 0 ? info.snippets[0] : null
  const [snippetId, setSnippetId] = useState(first ? first.id : '')
  const [start, setStart] = useState(first && first.from ? first.from.slice(0, 16) : '')
  const [end, setEnd] = useState('')
  const [minValue, setMinValue] = useState('')
  const [maxValue, setMaxValue] = useState('')
  const [filterColumn, setFilterColumn] = useState('') // '' = no filter
  const [filterValue, setFilterValue] = useState('')
  const [resample, setResample] = useState('')         // '', 'hour' or 'day'
  const [aggregate, setAggregate] = useState('mean')   // 'mean', 'min' or 'max'
  const [limit, setLimit] = useState(100)

  // The columns this series can be filtered on (e.g. a quality code), from the backend
  const [filterColumns, setFilterColumns] = useState(null) // null = still loading
  const [filterNote, setFilterNote] = useState('')

  // The answer from the backend
  const [result, setResult] = useState(null)
  const [requestUrl, setRequestUrl] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  // The snippet that is picked right now. Its time window can differ from the
  // group's, if the snippet states its own tss:from or tss:until.
  const chosen = info.snippets.find((snippet) => snippet.id === snippetId)

  // The column picked for the filter (undefined when there is no filter)
  const pickedColumn = filterColumns ? filterColumns.find((column) => column.name === filterColumn) : undefined

  // Every time another series is picked: ask which columns it can be filtered on
  useEffect(() => {
    if (snippetId === '') return
    let ignore = false // see App.jsx: throw away an answer that arrives too late

    const params = new URLSearchParams({ snippet: snippetId })
    fetch(`${API_URL}/endpoints/${info.id}/filter-columns?${params}`)
      .then((response) => response.json())
      .then((data) => {
        if (ignore) return
        if (data.detail) {
          // Not a real error for the form: there is just nothing to filter on
          setFilterColumns([])
          setFilterNote(errorText(data.detail))
        } else {
          setFilterColumns(data)
          setFilterNote('')
        }
      })
      .catch((err) => {
        if (!ignore) setError(fetchErrorText(err))
      })

    return () => {
      ignore = true
    }
  }, [info.id, snippetId])

  function pickSnippet(id) {
    setSnippetId(id)
    setResult(null)
    setError(null)
    setFilterColumns(null) // the new series may have other columns
    setFilterColumn('')
    setFilterValue('')
  }

  function pickFilterColumn(name) {
    setFilterColumn(name)
    // Start with the first value of that column
    const column = filterColumns.find((c) => c.name === name)
    setFilterValue(column ? column.values[0] : '')
  }

  function handleSubmit(event) {
    event.preventDefault() // stop the browser from reloading the page

    // URLSearchParams builds "snippet=...&start=...&..." and escapes special characters for us.
    // Number(limit) turns what was typed (e.g. "1e3") into a plain number (1000)
    const params = new URLSearchParams({
      snippet: snippetId,
      start: start,
      end: end,
      min_value: minValue,
      max_value: maxValue,
      filter_column: filterColumn,
      filter_value: filterColumn ? filterValue : '',
      resample: resample,
      aggregate: aggregate,
      limit: Number(limit),
    })
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
        {info.embedded && <p className="muted small">Reads the points embedded in the selected snippet. Numeric filters and aggregation apply to numeric series; text series support time filtering.</p>}
        <p className="muted small">
          {chosen && <>This series runs from {chosen.from} until {chosen.until} (until itself not included). </>}
          All times are UTC. Leave a field empty to not use it.
        </p>

        <form className="query-form" onSubmit={handleSubmit}>
          {/* 1. Which series, and which time range */}
          <div className="form-row">
            <label>
              Time series (snippet)
              <select value={snippetId} onChange={(event) => pickSnippet(event.target.value)}>
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
          </div>

          {/* 2. Which readings to keep */}
          <div className="form-row">
            <label>
              Values from
              <input type="number" step="any" placeholder="lowest" value={minValue}
                onChange={(event) => setMinValue(event.target.value)} />
            </label>
            <label>
              to
              <input type="number" step="any" placeholder="highest" value={maxValue}
                onChange={(event) => setMaxValue(event.target.value)} />
            </label>

            {filterColumns === null && <p className="muted small">Looking for columns to filter on…</p>}
            {filterColumns !== null && filterColumns.length > 0 && (
              <>
                <label>
                  Keep only rows where
                  <select value={filterColumn} onChange={(event) => pickFilterColumn(event.target.value)}>
                    <option value="">-- no filter --</option>
                    {filterColumns.map((column) => (
                      <option key={column.name} value={column.name}>
                        {column.header}
                      </option>
                    ))}
                  </select>
                </label>
                {pickedColumn && (
                  <label>
                    is
                    <select value={filterValue} onChange={(event) => setFilterValue(event.target.value)}>
                      {pickedColumn.values.map((value) => (
                        <option key={value} value={value}>
                          {value === '' ? '(empty)' : value}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
              </>
            )}
            {filterColumns !== null && filterColumns.length === 0 && (
              <p className="muted small long-text">No other columns to filter on. {filterNote}</p>
            )}
          </div>

          {/* 3. What to show */}
          <div className="form-row">
            <label>
              Show
              <select value={resample} onChange={(event) => setResample(event.target.value)}>
                <option value="">{RESAMPLE_NAMES['']}</option>
                <option value="hour">{RESAMPLE_NAMES.hour}</option>
                <option value="day">{RESAMPLE_NAMES.day}</option>
              </select>
            </label>
            <label>
              Each value is the
              {/* only used when the readings are grouped per hour or day */}
              <select value={aggregate} disabled={resample === ''} onChange={(event) => setAggregate(event.target.value)}>
                <option value="mean">mean</option>
                <option value="min">minimum</option>
                <option value="max">maximum</option>
              </select>
            </label>
            <label>
              Max rows
              <input type="number" min="1" max="5000" required value={limit}
                onChange={(event) => setLimit(event.target.value)} />
            </label>
            <button type="submit" disabled={loading}>
              {loading ? 'Running…' : 'Run query'}
            </button>
          </div>
        </form>
      </section>

      {error && <p className="error long-text">Error: {error}</p>}
      {result && <Summary result={result} />}
      {result && <QueryResult result={result} requestUrl={requestUrl} />}
    </>
  )
}

// The summary numbers and the gaps
function Summary({ result }) {
  const summary = result.summary
  const gaps = result.gaps
  const unit = result.unit ? ' ' + result.unit : ''

  return (
    <section className="card">
      <h3>Summary</h3>
      <p className="muted small">Over the single readings that passed all filters (before any grouping per hour or day). {result.numeric === false && 'This is a non-numeric series; numeric statistics are not applicable.'}</p>
      <div className="stats">
        <Stat name="Readings" value={summary.count} />
        <Stat name="Mean" value={summary.mean === null ? '—' : summary.mean + unit} />
        <Stat name="Minimum" value={summary.min ? summary.min.value + unit : '—'} note={summary.min && summary.min.time} />
        <Stat name="Maximum" value={summary.max ? summary.max.value + unit : '—'} note={summary.max && summary.max.time} />
        <Stat name="Empty cells" value={summary.empty} />
      </div>

      <h4>Gaps in the data</h4>
      {gaps.step === null ? (
        <p className="muted small">Too few readings in this time range to look for gaps.</p>
      ) : (
        <>
          <p className="small">
            Usually there is a reading every <strong>{gaps.step}</strong>. In this time range there are{' '}
            <strong>{gaps.count}</strong> places where readings are missing
            {gaps.count > 0 && <>, together <strong>{gaps.missing}</strong> of missing data</>}.
            (All rows in the time range count here, also the ones the filters left out.)
          </p>
          {gaps.longest.length > 0 && (
            <table className="facts">
              <tbody>
                {gaps.longest.map((gap) => (
                  <tr key={gap.from}>
                    <th>{gap.length}</th>
                    <td>no readings between {gap.from} and {gap.until}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </section>
  )
}

// One number in the summary
function Stat({ name, value, note }) {
  return (
    <div className="stat">
      <div className="muted small">{name}</div>
      <div className="stat-value">{value}</div>
      {note && <div className="muted small">at {note}</div>}
    </div>
  )
}

function QueryResult({ result, requestUrl }) {
  const grouped = result.resample !== ''
  const aggregateName = { mean: 'mean', min: 'minimum', max: 'maximum' }[result.aggregate]

  return (
    <section className="card">
      <h3>
        {result.totalRows} {grouped ? 'rows' : 'readings'}
        {result.limitReached && ` (showing the first ${result.readings.length}: see "Max rows")`}
      </h3>

      <p className="muted small long-text">
        Request sent: <a href={requestUrl} target="_blank" rel="noreferrer">{requestUrl}</a>
      </p>
      <p className="muted small">
        {result.embedded ? <>The backend read the embedded <code>tss:points</code> of this snippet and applied its time window{grouped && `, grouping readings per ${result.resample}`}.</> : <>How the backend answered: it opened <code>{result.csvFile}</code> (delimiter <code>{result.delimiter}</code>),
        took the time from column <code>{result.timeColumn}</code> (CSV header "{result.timeHeader}")
        and the value from column <code>{result.valueColumn}</code> (CSV header "{result.valueHeader}")
        {result.filterHeader && <>, kept only the rows where "{result.filterHeader}" is "{result.filterValue}"</>}
        {grouped && <>, and turned each {result.resample} into one value: the {aggregateName}</>}.</>}
      </p>

      <div className="table-scroll">
        <table className="data">
          <thead>
            <tr>
              <th>{grouped ? `Start of the ${result.resample}` : 'Time'}</th>
              <th>
                {grouped ? `${aggregateName} ` : 'Value '}{result.numeric !== false && `(${result.unit ? result.unit : 'unit unknown'})`}
              </th>
              {grouped && <th>Readings in it</th>}
            </tr>
          </thead>
          <tbody>
            {result.readings.map((reading) => (
              <tr key={reading.time}>
                <td>{reading.time}</td>
                <td>{reading.value === null ? '—' : typeof reading.value === 'object' ? JSON.stringify(reading.value) : String(reading.value)}</td>
                {grouped && <td>{reading.count}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
