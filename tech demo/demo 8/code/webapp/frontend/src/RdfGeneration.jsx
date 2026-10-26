import { useRef, useState } from 'react'
import { writeTurtle } from './rdf'
import { one, oneText, isEmbedded, RDFS_LABEL, TSS_DATA_FILE } from './tss'
import { fieldName } from './fields'

// The RDF generation tab: the source's data becomes SOSA observations (code/TSS2RDF/tss2rdf.py
// in the backend for a bundled endpoint; for your own endpoint the same rules in a Web Worker in
// this browser tab, see dataApi.js). We show a preview, and a link to download the whole graph.
// The preview is Turtle: we parse it here in the browser and write it out again.
export function RdfGeneration({ rdf, api }) {
  const [result, setResult] = useState(null)   // the numbers and links (from api.generateRdf)
  const [preview, setPreview] = useState(null) // { turtle, triples }: the parsed preview
  const [previewError, setPreviewError] = useState(null)
  const [error, setError] = useState(null)
  const [running, setRunning] = useState(false)

  // Each click on Generate gets a number. An answer for an older click (when you clicked
  // again before it arrived) is thrown away, like the "ignore" trick in App.jsx.
  const runNumber = useRef(0)

  // Where the data is: from the metadata
  const embedded = isEmbedded(rdf.store, rdf.source) === true
  const dataFile = one(rdf.store, rdf.source, TSS_DATA_FILE)

  function generate() {
    runNumber.current += 1
    const thisRun = runNumber.current
    setRunning(true)
    setResult(null)
    setPreview(null)
    setPreviewError(null)
    setError(null)

    api.generateRdf()
      .then((data) => {
        if (thisRun !== runNumber.current) return
        setResult(data)
        loadPreview(data, thisRun)
      })
      .catch((err) => {
        if (thisRun === runNumber.current) setError(err.message)
      })
      .finally(() => {
        if (thisRun === runNumber.current) setRunning(false)
      })
  }

  // Get the preview (Turtle with the first observations) parsed into a store (data.preview(),
  // see dataApi.js), and write the store out again as Turtle. So the page shows what an RDF
  // parser read.
  function loadPreview(data, thisRun) {
    let triples = 0
    data.preview()
      .then((loaded) => {
        triples = loaded.store.size
        // getQuads(null, null, null, null): every quad; the prefixes keep the text short (sosa:, xsd:, ...)
        return writeTurtle(loaded.store.getQuads(null, null, null, null), loaded.prefixes)
      })
      .then((turtle) => {
        if (thisRun === runNumber.current) setPreview({ turtle: turtle, triples: triples })
      })
      .catch((err) => {
        if (thisRun === runNumber.current) setPreviewError(err.message)
      })
  }

  return (
    <section className="card">
      <h3>RDF generation</h3>
      <p>
        Turn {embedded ? 'the points embedded in the Turtle file' : <>the data file <code>{dataFile ? dataFile.value : '(no tss:dataFile)'}</code></>} into
        SOSA observations, then view them (a Turtle preview) or download them (N-Triples).
        {api.inBrowser && ' This runs here, in this browser tab: the observations are not sent anywhere.'}
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
            {sizeText(result.bytes)}
          </p>
          <table className="facts">
            <tbody>
              {/* One line per field: the answer has its IRI and a count; its column name and label are in the metadata */}
              {result.series.map((series) => {
                // series.field is the field's full IRI: we look it up in the metadata store
                const label = oneText(rdf.store, series.field, RDFS_LABEL)
                return (
                  <tr key={series.field}>
                    {/* hovering shows the full IRI of the field */}
                    <th title={series.field}><code>{fieldName(rdf.store, series.field, rdf.prefixes)}</code></th>
                    <td>
                      {series.observations} observations
                      {label !== '' && <span className="muted small"> ({label})</span>}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {result.previewTruncated && (
            <p className="muted small">
              The preview shows the first {result.previewObservations} observations. The download has all {result.observations}.
            </p>
          )}
          <p>
            <a className="rdf-download" href={result.downloadUrl} download={result.filename}>Download N-Triples (.nt)</a>
          </p>

          {previewError && <p className="error long-text" role="alert">Could not show the preview: {previewError}</p>}
          {preview === null && previewError === null && <p role="status">Loading the preview…</p>}
          {preview && (
            <>
              <p className="muted small">
                The preview, parsed in the browser ({preview.triples} triples) and written out again as Turtle:
              </p>
              <pre className="rdf-preview" tabIndex={0} aria-label="Generated RDF graph">{preview.turtle}</pre>
            </>
          )}
        </div>
      )}
    </section>
  )
}

// A file size to read: "3.2 kB" or "52.7 MB"
function sizeText(bytes) {
  if (bytes < 1000000) return (bytes / 1000).toFixed(1) + ' kB'
  return (bytes / 1000000).toFixed(1) + ' MB'
}
