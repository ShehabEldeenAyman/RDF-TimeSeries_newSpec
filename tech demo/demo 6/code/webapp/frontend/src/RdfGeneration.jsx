import { useEffect, useRef, useState } from 'react'
import { API_URL, errorText, fetchErrorText } from './config'

export function RdfGeneration({ info }) {
  const parquet = info.dataFormat === 'http://publications.europa.eu/resource/authority/file-type/PARQUET'
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [running, setRunning] = useState(false)
  const controller = useRef(null)
  const downloadUrl = useRef(null)
  useEffect(() => () => {
    controller.current?.abort()
    if (downloadUrl.current) URL.revokeObjectURL(downloadUrl.current)
  }, [])

  async function generate() {
    controller.current?.abort()
    const request = new AbortController()
    controller.current = request
    if (downloadUrl.current) URL.revokeObjectURL(downloadUrl.current)
    downloadUrl.current = null
    setRunning(true)
    setResult(null)
    setError(null)
    try {
      const response = await fetch(`${API_URL}/endpoints/${info.id}/rdf`, {
        method: 'POST', signal: request.signal,
      })
      const data = await response.json()
      if (request.signal.aborted) return
      if (data.detail) setError(errorText(data.detail))
      else {
        if (!data.downloadUrl) downloadUrl.current = URL.createObjectURL(new Blob([data.turtle], { type: 'text/turtle;charset=utf-8' }))
        setResult({ ...data, downloadUrl: data.downloadUrl || downloadUrl.current })
      }
    } catch (err) {
      if (err.name !== 'AbortError') setError(fetchErrorText(err))
    } finally {
      if (!request.signal.aborted) setRunning(false)
    }
  }

  return <section className="card">
    <h3>RDF generation</h3>
    {typeof info.embedded !== 'boolean' ? <p>Cannot generate RDF until the endpoint declares its storage profile.</p> : <>
      <p>Expand {info.embedded ? 'embedded readings' : parquet ? 'external Parquet readings using their TSS context mappings' : 'external CSV readings using their CSVW mappings'} into an RDF graph, then view or download it as Turtle.</p>
      <p className="muted small">Each reading is a standalone RDF node with its type, time, value and shared domain metadata. Points without identifiers become blank nodes. Source containers, TSS vocabulary and references to source snippets are omitted.</p>
      <button disabled={running} onClick={generate}>{running ? 'Generating…' : 'Generate RDF'}</button>
    </>}
    {error && <p className="error long-text" role="alert">{error}</p>}
    {result && <div className="result" aria-live="polite">
      <p><strong>{result.points}</strong> points from <strong>{result.series.length}</strong> series · <strong>{result.triples}</strong> RDF triples</p>
      {result.previewTruncated && <p className="muted small">The preview shows the first {result.previewPoints} readings. Download Turtle includes all {result.points} readings.</p>}
      <p><a className="rdf-download" href={result.downloadUrl} download={result.filename}>Download Turtle (.ttl)</a></p>
      <pre className="rdf-preview" tabIndex={0} aria-label="Generated RDF graph">{result.turtle}</pre>
    </div>}
  </section>
}
