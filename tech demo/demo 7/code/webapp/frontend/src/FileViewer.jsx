import { useState, useEffect } from 'react'
import { errorText, fetchErrorText } from './config'

// The Files tab's viewer for an external data file (tss:dataFile): its first lines, from the
// backend, and a download icon. (Files.jsx picks this one, or PointsViewer.jsx for the points
// embedded in the metadata.)

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

// Shows the data file of an external source: its first lines, as text from the backend
// (GET /api/endpoints/{id}/data; a Parquet file is previewed as CSV text). The answer is
// JSON { found, note, text } with only the computed preview: where the file is comes
// from the metadata (location).
//   url          the /data route of the endpoint
//   location     the tss:dataFile IRI from the metadata store, to show where the file is
//   downloadUrl  optional: when it is given, the card shows a download icon
export function FileViewer({ title, url, location, downloadUrl, downloadLabel = 'Download the whole file' }) {
  const [file, setFile] = useState(null) // { found, note, text }
  const [error, setError] = useState(null)

  useEffect(() => {
    // An AbortController cancels the request when the card disappears before the answer arrives
    const controller = new AbortController()
    fetch(url, { signal: controller.signal })
      .then((response) => response.json())
      .then((data) => {
        if (data.detail) setError(errorText(data.detail)) // FastAPI puts its error message in "detail"
        else setFile(data)
      })
      .catch((err) => {
        if (err.name !== 'AbortError') setError(fetchErrorText(err))
      })
    return () => controller.abort()
  }, [url])

  // The file's IRI as text: the browser can't open it (data/ is not served), so no link
  const where = location ? <code>{location}</code> : <span>(the source has no tss:dataFile)</span>

  // First decide what goes inside the card...
  let content
  if (error) {
    content = <p className="error">Error: {error}</p>
  } else if (file === null) {
    content = <p>Loading…</p>
  } else if (!file.found) {
    content = <p className="error long-text">{file.note} {where}</p>
  } else {
    content = (
      <>
        <p className="muted small long-text">
          {where} {file.note && <span>({file.note})</span>}
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
