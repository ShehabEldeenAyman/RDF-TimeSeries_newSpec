import { useEffect, useRef, useState } from 'react'
import { API_URL, errorText, fetchErrorText } from './config'

export function Shacl({ info }) {
  const [result, setResult] = useState(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState(null)
  const [customFile, setCustomFile] = useState(null)
  const [reading, setReading] = useState(false)
  const fileInput = useRef(null)
  const controller = useRef(null)
  useEffect(() => () => controller.current?.abort(), [])
  const known = typeof info.embedded === 'boolean'
  const shapes = info.embedded ? 'embedded_shapes.ttl' : info.csvwApplicable ? 'external_csv_shapes.ttl' : 'external_non_csv_shapes.ttl'
  const profile = info.embedded ? 'embedded time series' : info.csvwApplicable ? 'external CSV time series' : 'external non-CSV time series'

  async function selectFile(event) {
    const file = event.target.files[0]
    setResult(null)
    setError(null)
    setCustomFile(null)
    if (!file) return
    if (file.size > 5_000_000) {
      setError('The shapes file must be at most 5 MB.')
      event.target.value = ''
      return
    }
    setReading(true)
    try {
      const turtle = await file.text()
      if (!turtle.trim()) throw new Error('The shapes file is empty.')
      setCustomFile({ filename: file.name, turtle })
    } catch (err) {
      setError(`Could not read the shapes file: ${err.message}`)
      if (fileInput.current) fileInput.current.value = ''
    } finally {
      setReading(false)
    }
  }

  function useDefaults() {
    setCustomFile(null)
    setResult(null)
    setError(null)
    if (fileInput.current) fileInput.current.value = ''
  }

  async function runValidation() {
    controller.current?.abort()
    const request = new AbortController()
    controller.current = request
    setRunning(true)
    setResult(null)
    setError(null)
    try {
      const response = await fetch(`${API_URL}/endpoints/${info.id}/shacl`, {
        signal: request.signal,
        ...(customFile ? {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(customFile),
        } : {}),
      })
      const data = await response.json()
      if (data.detail) setError(errorText(data.detail))
      else setResult(data)
    } catch (err) {
      if (err.name !== 'AbortError') setError(fetchErrorText(err))
    } finally {
      if (!request.signal.aborted) setRunning(false)
    }
  }

  return <section className="card">
    <h3>SHACL conformance</h3>
    {known ? <p>Detected <strong>{profile}</strong> using <code>tss:embedded {String(info.embedded)}</code>{!info.embedded && <> and <code>dcterms:format</code></>}.</p>
      : <p className="muted">Cannot select default shapes: the group must state one boolean tss:embedded. You can upload custom shapes instead.</p>}
    <p><label htmlFor="shacl-file">Upload custom SHACL shapes (.ttl)</label>{' '}
      <input ref={fileInput} id="shacl-file" type="file" accept=".ttl,text/turtle" disabled={running || reading} onChange={selectFile} /></p>
    {reading && <p role="status">Reading shapes file…</p>}
    {customFile ? <p>Using custom shapes: <strong>{customFile.filename}</strong>. These replace the default shapes for this test.{' '}
      <button className="secondary" disabled={running || reading} onClick={useDefaults}>Use default shapes</button></p>
      : known && <p>Using default shapes: <code>{shapes}</code>.</p>}
    <p className="muted small">Checks the loaded Turtle against the selected shapes. Warnings are shown but do not make the test fail. Custom files are used for this session and do not replace the bundled files. CSV contents are checked separately in CSVW validation.</p>
    <button disabled={running || reading || (!known && !customFile)} onClick={runValidation}>{running ? 'Checking…' : 'Run conformance test'}</button>
    {error && <p className="error long-text" role="alert">{error}</p>}
    {result && <div className="result" aria-live="polite">
      <p><span className={result.conforms ? 'badge ok' : 'badge bad'}>{result.conforms ? 'Conforms' : 'Does not conform'}</span>{' '}
        {result.violations} violations · {result.warnings} warnings · {result.infos} infos · {result.seconds} s</p>
      <p className="muted small">Shapes: {result.shapes} ({result.custom ? 'custom upload' : 'default'})</p>
      <pre className="shacl-report" tabIndex={0} aria-label="SHACL validation report">{result.report}</pre>
    </div>}
  </section>
}
