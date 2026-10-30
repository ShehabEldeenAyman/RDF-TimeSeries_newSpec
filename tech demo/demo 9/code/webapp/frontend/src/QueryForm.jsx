import { useState, useEffect } from 'react'
import {
  one, oneText, readFields, isEmbedded, isNumericDatatype, shortName, dateTimeValue,
  TSS_TIME_FIELD, TSS_FROM, TSS_UNTIL, TSS_DATA_FILE, TSS_FORMAT, TSS_CSV,
  TSS_DELIMITER, TSS_DATATYPE, TSS_UNIT,
} from './tss'
import { firstField, fieldName, fieldText, isTimeField, unitText } from './fields'
import { SummaryCheck } from './SummaryCheck'

// The words the form and the results use for the backend's options
const RESAMPLE_NAMES = { '': 'every reading', hour: 'one value per hour', day: 'one value per day' }

// The query form. Everything it says about the source and its fields comes from the
// metadata (rdf.store, the parsed Turtle). The api (dataApi.js) only reads the data and
// computes the readings, the summary and the gaps: the backend for a bundled endpoint, this
// browser tab for your own endpoint.
export function QueryForm({ rdf, api }) {
  const store = rdf.store
  const source = rdf.source
  const from = one(store, source, TSS_FROM)   // a literal like "2021-03-03T23:45:00Z"^^xsd:dateTime, or null
  const until = one(store, source, TSS_UNTIL)

  // Every field except the time field is a time series that can be queried
  const series = []
  for (const field of readFields(store, source)) {
    if (!isTimeField(store, source, field)) series.push(field)
  }

  // What the user fills in. A field is picked by its IRI. The form starts with the
  // source's whole time window: from tss:from until tss:until.
  const [fieldIri, setFieldIri] = useState(firstSeries(store, source, series))
  const [start, setStart] = useState(inputTime(from, false))
  const [end, setEnd] = useState(inputTime(until, true))
  const [minValue, setMinValue] = useState('')
  const [maxValue, setMaxValue] = useState('')
  const [filterField, setFilterField] = useState('') // the IRI of the field to filter on; '' = no filter
  const [filterValue, setFilterValue] = useState('')
  const [resample, setResample] = useState('')         // '', 'hour' or 'day'
  const [aggregate, setAggregate] = useState('mean')   // 'mean', 'min' or 'max'
  const [limit, setLimit] = useState(100)

  // The fields this series can be filtered on (e.g. a quality code), from the api:
  // [{ field: '<IRI>', values: ['100', '180'] }, ...]
  const [filterFields, setFilterFields] = useState(null) // null = still loading
  const [filterNote, setFilterNote] = useState('')

  // The answer from the api, and what we asked (to explain the answer)
  const [result, setResult] = useState(null)
  const [request, setRequest] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  // Is the picked field numeric? That depends on its tss:datatype IRI.
  const numeric = isNumeric(store, fieldIri)

  // The filter field that is picked (undefined when there is no filter)
  const pickedFilter = filterFields ? filterFields.find((entry) => entry.field === filterField) : undefined

  // Every time another series is picked: ask which fields it can be filtered on
  useEffect(() => {
    if (fieldIri === '') return
    let ignore = false // see App.jsx: throw away an answer that arrives too late

    api.filterColumns(fieldIri)
      .then((data) => {
        if (ignore) return
        setFilterFields(data)
        setFilterNote('')
      })
      .catch((err) => {
        if (ignore) return
        // Not a real error for the form: there is just nothing to filter on
        setFilterFields([])
        setFilterNote(err.message)
      })

    return () => {
      ignore = true
    }
  }, [api, fieldIri])

  function pickField(iri) {
    setFieldIri(iri)
    setResult(null)
    setError(null)
    setFilterFields(null) // the new series may have other fields to filter on
    setFilterField('')
    setFilterValue('')
    // Value ranges and grouping only work for numbers: clear them for a text field
    if (!isNumeric(store, iri)) {
      setMinValue('')
      setMaxValue('')
      setResample('')
    }
  }

  function pickFilterField(iri) {
    setFilterField(iri)
    // Start with the first value of that field
    const entry = filterFields.find((e) => e.field === iri)
    setFilterValue(entry ? entry.values[0] : '')
  }

  function handleSubmit(event) {
    event.preventDefault() // stop the browser from reloading the page

    // URLSearchParams builds "field=...&start=...&..." and escapes special characters
    // (like the # in a field IRI) for us. Only the parts that are used go in.
    const params = new URLSearchParams()
    params.set('field', fieldIri)
    if (start !== '') params.set('start', start)
    if (end !== '') params.set('end', end)
    if (numeric && minValue !== '') params.set('min_value', minValue)
    if (numeric && maxValue !== '') params.set('max_value', maxValue)
    if (filterField !== '') {
      params.set('filterField', filterField)
      // Always sent with filterField: '' means "the rows without a value there"
      params.set('filter_value', filterValue)
    }
    if (numeric && resample !== '') {
      params.set('resample', resample)
      params.set('aggregate', aggregate)
    }
    // Number(limit) turns what was typed (e.g. "1e3") into a plain number (1000)
    params.set('limit', Number(limit))

    // Remember what we asked: the answer only holds numbers, and the explanation
    // under it reads the fields of this request from the metadata.
    //   url: the address of the request for a bundled endpoint; null for your own endpoint
    //        (the browser computes the answer, nothing is sent anywhere)
    setRequest({ url: api.queryAddress(params), params: params.toString(), field: fieldIri, filterField: filterField })
    setLoading(true)
    setResult(null)
    setError(null)

    api.query(params)
      .then((data) => setResult(data))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }

  if (series.length === 0) return <p className="muted">This data source has no fields to query.</p>

  return (
    <>
      <section className="card">
        <h3>Query a time series</h3>
        {isEmbedded(store, source) === true && <p className="muted small">Reads the points embedded in the Turtle file (tss:pointOf).</p>}
        <p className="muted small">
          Value ranges and mean/min/max apply to numeric fields (a numeric tss:datatype); text fields can be filtered on time and on other fields.
        </p>
        <p className="muted small">
          {from && until && <>The source runs from {from.value} until {until.value} (<code>tss:from</code>, <code>tss:until</code>; until itself not included); the form starts with that whole window. </>}
          All times are UTC. Leave a field empty to not use it.
        </p>

        <form className="query-form" onSubmit={handleSubmit}>
          {/* 1. Which series, and which time range */}
          <div className="form-row">
            <label>
              Time series (field)
              <select value={fieldIri} onChange={(event) => pickField(event.target.value)}>
                {series.map((field) => (
                  <option key={field.value} value={field.value}>
                    {fieldText(store, field, rdf.prefixes)}
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
              <input type="number" step="any" placeholder={numeric ? 'lowest' : 'numbers only'} value={minValue}
                disabled={!numeric} onChange={(event) => setMinValue(event.target.value)} />
            </label>
            <label>
              to
              <input type="number" step="any" placeholder={numeric ? 'highest' : 'numbers only'} value={maxValue}
                disabled={!numeric} onChange={(event) => setMaxValue(event.target.value)} />
            </label>

            {filterFields === null && <p className="muted small">Looking for fields to filter on…</p>}
            {filterFields !== null && filterFields.length > 0 && (
              <>
                <label>
                  Keep only rows where
                  <select value={filterField} onChange={(event) => pickFilterField(event.target.value)}>
                    <option value="">-- no filter --</option>
                    {filterFields.map((entry) => (
                      <option key={entry.field} value={entry.field}>
                        {fieldText(store, entry.field, rdf.prefixes)}
                      </option>
                    ))}
                  </select>
                </label>
                {pickedFilter && (
                  <label>
                    is
                    <select value={filterValue} onChange={(event) => setFilterValue(event.target.value)}>
                      {pickedFilter.values.map((value) => (
                        <option key={value} value={value}>
                          {value === '' ? '(empty)' : value}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
              </>
            )}
            {filterFields !== null && filterFields.length === 0 && (
              <p className="muted small long-text">No other fields to filter on. {filterNote}</p>
            )}
          </div>

          {/* 3. What to show */}
          <div className="form-row">
            <label>
              Show
              {/* grouping per hour or day needs numbers, so only numeric fields can use it */}
              <select value={resample} disabled={!numeric} onChange={(event) => setResample(event.target.value)}>
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

      {/* key: a new check for each picked field, so an answer for another field is never shown */}
      <SummaryCheck key={fieldIri} rdf={rdf} api={api} fieldIri={fieldIri} />

      {error && <p className="error long-text">Error: {error}</p>}
      {result && <Summary result={result} request={request} rdf={rdf} />}
      {result && <QueryResult result={result} request={request} rdf={rdf} inBrowser={api.inBrowser} />}
    </>
  )
}

// The summary numbers and the gaps
function Summary({ result, request, rdf }) {
  const summary = result.summary
  const gaps = result.gaps
  // The unit is not in the answer: it is in the metadata, on the field we asked for
  const unitName = unitText(rdf.store, one(rdf.store, request.field, TSS_UNIT), rdf.prefixes)
  const unit = unitName !== '' ? ' ' + unitName : ''
  const numeric = isNumeric(rdf.store, request.field)

  return (
    <section className="card">
      <h3>Summary</h3>
      <p className="muted small">Over the single readings that passed all filters (before any grouping per hour or day). {!numeric && 'This is a non-numeric series; numeric statistics are not applicable.'}</p>
      <div className="stats">
        <Stat name="Readings" value={summary.count} />
        <Stat name="Mean" value={summary.mean === null ? '—' : summary.mean + unit} />
        <Stat name="Median" value={summary.median === null ? '—' : summary.median + unit} />
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

// inBrowser: true when this browser tab computed the answer (your own endpoint)
function QueryResult({ result, request, rdf, inBrowser }) {
  const store = rdf.store
  const source = rdf.source
  const grouped = result.resample !== ''
  const aggregateName = { mean: 'mean', min: 'minimum', max: 'maximum' }[result.aggregate]

  // How the data was read: the metadata says it, so we read it from there
  const embedded = isEmbedded(store, source) === true
  const format = one(store, source, TSS_FORMAT)
  const isCsv = format !== null && format.value === TSS_CSV
  const dataFile = one(store, source, TSS_DATA_FILE)
  const dataFileText = dataFile ? dataFile.value : '(no tss:dataFile)'
  const delimiter = oneText(store, source, TSS_DELIMITER)
  const timeField = one(store, source, TSS_TIME_FIELD)
  const datatype = one(store, request.field, TSS_DATATYPE)
  const unitName = unitText(store, one(store, request.field, TSS_UNIT), rdf.prefixes)
  const numeric = isNumeric(store, request.field)

  return (
    <section className="card">
      <h3>
        {result.totalRows} {grouped ? 'rows' : 'readings'}
        {result.limitReached && ` (showing the first ${result.readings.length}: see "Max rows")`}
      </h3>

      {request.url !== null ? (
        <p className="muted small long-text">
          Request sent: <a href={request.url} target="_blank" rel="noreferrer">{request.url}</a>
        </p>
      ) : (
        <p className="muted small long-text">
          Computed in this browser tab (nothing was sent anywhere), with the same parameters as the
          server's <code>/query</code>: <code>{request.params}</code>
        </p>
      )}
      <p className="muted small">
        {inBrowser ? 'How your browser answered' : 'How the backend answered'}: it{' '}
        {embedded && <>read the points embedded in the Turtle file (the nodes with <code>tss:pointOf</code>)</>}
        {!embedded && isCsv && <>opened <code>{dataFileText}</code> (CSV{delimiter !== '' && <>, delimiter <code>{delimiter}</code></>})</>}
        {!embedded && !isCsv && <>opened <code>{dataFileText}</code> ({format ? shortName(format, rdf.prefixes) : 'no tss:format'})</>},
        took the time from column "{timeField ? fieldName(store, timeField, rdf.prefixes) : '(none)'}" (<code>tss:timeField</code>)
        and the value from column "{fieldName(store, request.field, rdf.prefixes)}" ({datatype ? shortName(datatype, rdf.prefixes) : 'no tss:datatype'}),
        kept only the readings in the source's time window
        {request.filterField !== '' && (
          <>
            , kept only the rows where "{fieldName(store, request.filterField, rdf.prefixes)}"{' '}
            {/* filterValue '' means: the rows without a value there */}
            {result.filterValue === '' ? 'has no value' : <>is "{result.filterValue}"</>}
          </>
        )}
        {grouped && <>, and turned each {result.resample} into one value: the {aggregateName}</>}.
      </p>

      <div className="table-scroll">
        <table className="data">
          <thead>
            <tr>
              <th>{grouped ? `Start of the ${result.resample}` : 'Time'}</th>
              <th>
                {grouped ? `${aggregateName} ` : 'Value '}{numeric && `(${unitName !== '' ? unitName : 'unit unknown'})`}
              </th>
              {grouped && <th>Readings in it</th>}
            </tr>
          </thead>
          <tbody>
            {result.readings.map((reading, index) => (
              <tr key={index}>
                <td>{reading.time}</td>
                <td>{reading.value === null ? '—' : String(reading.value)}</td>
                {grouped && <td>{reading.count}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

// --- Reading the metadata -------------------------------------------------------
// field may be an IRI (a string) or a NamedNode; the tss.js helpers accept both.

// Is this field numeric? Only when its tss:datatype is a numeric XSD datatype (isNumericDatatype
// in tss.js). Only a numeric field has value ranges and mean/min/max.
function isNumeric(store, field) {
  if (field === '' || field === null) return false
  return isNumericDatatype(one(store, field, TSS_DATATYPE))
}

// The series to show first: the one firstField (fields.js) picks, e.g. the first
// measured series. The time field is no series, so then simply the first series.
// Gives its IRI, or '' when there is no series.
function firstSeries(store, source, series) {
  const first = firstField(store, source)
  if (first !== null && !isTimeField(store, source, first)) return first.value
  return series.length > 0 ? series[0].value : ''
}

// A tss:from or tss:until literal as the text a datetime-local input wants, in UTC:
// "2021-03-03T23:45". '' when there is none, or it is not an xsd:dateTime.
//   roundUp: the input only has whole minutes. For the end of the range we round a time
//            like 23:15:30 up to 23:16, so no reading inside the window is left out.
function inputTime(literal, roundUp) {
  // dateTimeValue (tss.js) looks at the datatype first (the text only means a date-time
  // when the literal says so), and reads a time without a timezone as UTC, as the backend does
  const time = dateTimeValue(literal)
  if (time === null) return ''

  const minute = 60 * 1000 // in milliseconds
  if (roundUp) time.setTime(Math.ceil(time.getTime() / minute) * minute)

  // The parts of the time in UTC (getUTC...), each written with two digits
  const two = (number) => String(number).padStart(2, '0')
  return time.getUTCFullYear() + '-' + two(time.getUTCMonth() + 1) + '-' + two(time.getUTCDate()) +
    'T' + two(time.getUTCHours()) + ':' + two(time.getUTCMinutes())
}
