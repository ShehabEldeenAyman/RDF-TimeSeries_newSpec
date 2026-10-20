// One "name | value" row in a small table. Shows a dash when the endpoint has no value.
function Fact({ name, value }) {
  return (
    <tr>
      <th>{name}</th>
      <td>{value ? value : '—'}</td>
    </tr>
  )
}

// What a field does in the source, e.g. "time, key" or "describes Value"
function roleText(field) {
  const roles = []
  if (field.isTime) roles.push('time')
  if (field.isKey) roles.push('key')
  if (field.describesField) roles.push('describes ' + field.describesField)
  return roles.length > 0 ? roles.join(', ') : '—'
}

// The allowed values, e.g. "0 to 10000". "…" when only one side is given.
function rangeText(field) {
  if (field.minimum === null && field.maximum === null) return '—'
  const minimum = field.minimum === null ? '…' : field.minimum
  const maximum = field.maximum === null ? '…' : field.maximum
  return minimum + ' to ' + maximum
}

// Everything one metadata endpoint holds. All of it comes from the backend in one object: info
export function Overview({ info }) {
  // Only show the "Index" column when at least one field has a tss:columnIndex
  const hasIndexes = info.fields.some((field) => field.columnIndex !== null)

  return (
    <>
      <section className="card">
        <h2>{info.label}</h2>
        <p className="muted long-text">
          {info.online ? 'Online metadata endpoint: ' : 'Metadata endpoint file: '}
          <code>{info.file}</code>
        </p>
      </section>

      <div className="two-columns">
        <section className="card">
          <h3>Source</h3>
          <p className="muted small">
            {info.embedded ? 'The readings are embedded in the Turtle file, in tss:points.' : 'The readings are in an external data file (tss:dataFile).'}
          </p>
          <table className="facts">
            <tbody>
              <Fact name="IRI" value={info.source} />
              <Fact name="tss:embedded" value={info.embedded === null ? 'not stated' : String(info.embedded)} />
              <Fact name="tss:format" value={info.format} />
              {!info.embedded && <Fact name="tss:dataFile" value={info.dataFile} />}
              <Fact name="tss:mutable" value={info.mutable} />
              <Fact name="tss:producedBy" value={info.producedBy} />
              <Fact name="tss:from" value={info.from} />
              <Fact name="tss:until" value={info.until} />
              <Fact name="tss:timeField" value={info.timeField} />
              <Fact name="tss:key" value={info.key.join(', ')} />
            </tbody>
          </table>
        </section>

        <section className="card">
          <h3>Reading the data</h3>
          {info.embedded && (
            <>
              <p className="muted small">How the points in tss:points are written.</p>
              <table className="facts">
                <tbody>
                  <Fact name="tss:pointLayout" value={info.pointLayout} />
                </tbody>
              </table>
            </>
          )}
          {!info.embedded && info.format === 'tss:CSV' && (
            <>
              <p className="muted small">How to split the CSV file into rows and columns.</p>
              <table className="facts">
                <tbody>
                  <Fact name="tss:delimiter" value={info.delimiter} />
                  <Fact name="tss:header" value={info.header} />
                  <Fact name="tss:encoding" value={info.encoding} />
                </tbody>
              </table>
            </>
          )}
          {!info.embedded && info.format !== 'tss:CSV' && (
            <p className="muted small">
              No reading settings needed: a {info.format ? info.format : 'file of this format'} file describes its own columns.
            </p>
          )}
        </section>
      </div>

      <section className="card">
        <h3>Fields ({info.fields.length})</h3>
        <p className="muted small">
          One field = one {info.embedded ? 'key in each embedded point' : 'column in the data file'}.
          A field that describes another one holds extra information about that field's values, such as a quality code.
        </p>
        <div className="table-scroll">
          <table className="data">
            <thead>
              <tr>
                <th>Column</th>
                {hasIndexes && <th>Index</th>}
                <th>Role</th>
                <th>Label</th>
                <th>Datatype</th>
                <th>Unit</th>
                <th>Observed property</th>
                <th>Sensor</th>
                <th>Feature of interest</th>
                <th>Range</th>
                <th>Required</th>
              </tr>
            </thead>
            <tbody>
              {info.fields.map((field) => (
                <tr key={field.id}>
                  <td><code>{field.columnName}</code></td>
                  {hasIndexes && <td>{field.columnIndex !== null ? field.columnIndex : '—'}</td>}
                  <td>{roleText(field)}</td>
                  <td>
                    {field.label}
                    {field.comments.map((comment) => (
                      <div key={comment} className="muted small">{comment}</div>
                    ))}
                  </td>
                  <td>
                    {field.datatype}
                    {field.language && <span className="muted small"> ({field.language})</span>}
                  </td>
                  <td>{field.unit ? field.unit : '—'}</td>
                  <td>{field.observedProperty ? field.observedProperty : '—'}</td>
                  <td>{field.sensor ? field.sensor : '—'}</td>
                  <td>{field.featureOfInterest ? field.featureOfInterest : '—'}</td>
                  <td>{rangeText(field)}</td>
                  <td>{field.required ? field.required : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {info.otherProperties.length > 0 && (
        <section className="card">
          <h3>Other statements about the source</h3>
          <p className="muted small">For example its licence and provenance. Hover over a value to see the exact RDF.</p>
          <table className="facts">
            <tbody>
              {info.otherProperties.map((line, index) => (
                <tr key={index}>
                  <th>{line.property}</th>
                  <td title={line.value.hover}>
                    {line.value.link ? <a href={line.value.link} target="_blank" rel="noreferrer">{line.value.text}</a> : line.value.text}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <section className="card">
        <details>
          <summary>Show the raw Turtle file</summary>
          <pre>{info.turtle}</pre>
        </details>
      </section>
    </>
  )
}
