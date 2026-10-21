import { useEffect, useRef, useState } from 'react'
import { API_URL, errorText, fetchErrorText } from './config'

// The RDF generation tab: the backend turns the source's data into SOSA observations
// (code/TSS2RDF/tss2rdf.py). We show a preview, and a link to download the whole graph.
export function RdfGeneration({ info }) {
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [running, setRunning] = useState(false)
  const controller = useRef(null)

  // Leaving the tab (or the page) cancels a generation that is still running
  useEffect(() => () => controller.current?.abort(), [])

  async function generate() {
    controller.current?.abort()
    const request = new AbortController()
    controller.current = request
    setRunning(true)
    setResult(null)
    setError(null)
    try {
      const response = await fetch(`${API_URL}/endpoints/${info.id}/rdf`, { method: 'POST', signal: request.signal })
      const data = await response.json()
      if (request.signal.aborted) return
      if (data.detail) setError(errorText(data.detail))
      else setResult(data)
    } catch (err) {
      if (err.name !== 'AbortError') setError(fetchErrorText(err))
    } finally {
      if (!request.signal.aborted) setRunning(false)
    }
  }

  return (
    <section className="card">
      <h3>RDF generation</h3>
      <p>
        Turn {info.embedded ? 'the points embedded in the Turtle file' : <>the data file <code>{info.dataFile}</code></>} into
        SOSA observations, then view or download them as Turtle.
      </p>
      <p className="muted small">
        Every row gives one <code>sosa:Observation</code> for each field with a <code>tss:observedProperty</code>: its
        observed property, sensor, feature of interest and unit, the row's time as <code>sosa:resultTime</code>, and the
        value as <code>sosa:hasSimpleResult</code>, typed with the field's <code>tss:datatype</code>. Only rows inside the
        source's time window are used, and an empty cell gives no observation. Fields without an observed property (such
        as an id or a quality code) are not in the RDF.
      </p>
      <button disabled={running} onClick={generate}>{running ? 'Generating…' : 'Generate RDF'}</button>

      {error && <p className="error long-text" role="alert">{error}</p>}

      {result && (
        <div className="result" aria-live="polite">
          <p>
            <strong>{result.observations}</strong> observations · <strong>{result.triples}</strong> RDF triples ·{' '}
            {(result.bytes / 1000000).toFixed(1)} MB
          </p>
          <table className="facts">
            <tbody>
              {result.series.map((series) => (
                <tr key={series.column}>
                  <th><code>{series.column}</code></th>
                  <td>{series.observations} observations <span className="muted small">({series.label})</span></td>
                </tr>
              ))}
            </tbody>
          </table>
          {result.previewTruncated && (
            <p className="muted small">
              The preview shows the first {result.previewObservations} observations. The download has all {result.observations}.
            </p>
          )}
          <p>
            <a className="rdf-download" href={result.downloadUrl} download={result.filename}>Download Turtle (.ttl)</a>
          </p>
          <pre className="rdf-preview" tabIndex={0} aria-label="Generated RDF graph">{result.turtle}</pre>
        </div>
      )}
    </section>
  )
}
