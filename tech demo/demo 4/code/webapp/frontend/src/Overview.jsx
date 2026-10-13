// One "name | value" row in a small table. Shows a dash when the endpoint has no value.
function Fact({ name, value }) {
  return (
    <tr>
      <th>{name}</th>
      <td>{value ? value : '—'}</td>
    </tr>
  )
}

// Everything one metadata endpoint holds. All of it comes from the backend in one object: info
export function Overview({ info }) {
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
          <h3>Snippet group</h3>
          <p className="muted small">
            Stated on the group itself: the one CSV file all its snippets read, the CSVW file that describes it, and
            whether it changes.
          </p>
          <table className="facts">
            <tbody>
              <Fact name="IRI" value={info.group} />
              <Fact name="tss:embedded" value={info.embedded} />
              <Fact name="tss:dataFile" value={info.csvUrl} />
              <Fact name="tss:describedby" value={info.describedBy} />
              <Fact name="tss:mutable" value={info.mutable} />
              <Fact name="tss:producedBy" value={info.producedBy} />
            </tbody>
          </table>
        </section>

        <section className="card">
          <h3>Snippet template</h3>
          <p className="muted small">The defaults that every snippet in the group inherits.</p>
          <table className="facts">
            <tbody>
              <Fact name="tss:timeColumn" value={info.timeColumn} />
              <Fact name="tss:from" value={info.from} />
              <Fact name="tss:until" value={info.until} />
              <Fact name="tss:pointType" value={info.pointType} />
              <Fact name="tss:timePath" value={info.timePath} />
              <Fact name="tss:valuePath" value={info.valuePath} />
            </tbody>
          </table>
        </section>
      </div>

      <section className="card">
        <h3>Snippets ({info.snippets.length})</h3>
        <p className="muted small">One snippet = one time series = one value column in the CSV.</p>
        <div className="table-scroll">
          <table className="data">
            <thead>
              <tr>
                <th>Label</th>
                <th>Value column</th>
                <th>Unit</th>
                <th>Observed property</th>
                <th>Sensor</th>
                <th>Feature of interest</th>
                <th>Identifier</th>
              </tr>
            </thead>
            <tbody>
              {info.snippets.map((snippet) => (
                <tr key={snippet.id}>
                  <td>
                    {snippet.label}
                    {snippet.comment && <div className="muted small">{snippet.comment}</div>}
                  </td>
                  <td><code>{snippet.valueColumn}</code></td>
                  <td>{snippet.unit ? snippet.unit : 'unknown'}</td>
                  <td>{snippet.observedProperty}</td>
                  <td>{snippet.sensor}</td>
                  <td>{snippet.featureOfInterest}</td>
                  <td>{snippet.identifier ? snippet.identifier : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card">
        <details>
          <summary>Show the raw Turtle file</summary>
          <pre>{info.turtle}</pre>
        </details>
      </section>
    </>
  )
}
