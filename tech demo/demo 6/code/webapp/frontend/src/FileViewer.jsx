import { useState, useEffect } from 'react'
import { errorText, fetchErrorText } from './config'

// A small download icon. A normal link is enough here (no fetch):
// the backend answers with the file and tells the browser to save it.
function DownloadLink({ url, label }) {
  return (
    <a className="download" href={url} title={label} aria-label={label}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 3v12" />       {/* the arrow's line */}
        <path d="M7 10l5 5 5-5" />  {/* the arrow's head */}
        <path d="M5 21h14" />       {/* the line under it */}
      </svg>
    </a>
  )
}

// Shows one file that a metadata endpoint points to, or the points embedded in it.
// Data previews may be CSV text converted from Parquet.
// downloadUrl is optional: when it is given, the card shows a download icon.
export function FileViewer({ title, url, downloadUrl, downloadLabel = 'Download the whole file' }) {
  const [file, setFile] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    const controller = new AbortController()
    fetch(url, { signal: controller.signal })
      .then((response) => response.json())
      .then((data) => {
        if (data.detail) setError(errorText(data.detail))
        else setFile(data)
      })
      .catch((err) => {
        if (err.name !== 'AbortError') setError(fetchErrorText(err))
      })
    return () => controller.abort()
  }, [url])

  // First decide what goes inside the card...
  let content
  if (error) {
    content = <p className="error">Error: {error}</p>
  } else if (file === null) {
    content = <p>Loading…</p>
  } else if (!file.found) {
    content = <p className="error long-text">File not found: <code>{file.path}</code></p>
  } else {
    content = (
      <>
        <p className="muted small long-text">
          <code>{file.path}</code> {file.note && <span>({file.note})</span>}
          {downloadUrl && <DownloadLink url={downloadUrl} label={downloadLabel} />}
        </p>
        <pre>{file.text}</pre>
      </>
    )
  }

  // ...then draw the card
  return (
    <section className="card">
      <h3>{title}</h3>
      {content}
    </section>
  )
}
