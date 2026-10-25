import { useState, useEffect } from 'react'

// The Files tab's viewer for an external data file (tss:dataFile): its first lines and a download
// icon. (Files.jsx picks this one, or PointsViewer.jsx for the points embedded in the metadata.)

// A small download icon. A normal link is enough here (no fetch): the backend answers with the
// file and tells the browser to save it. For your own endpoint the link is a blob: address (the
// browser's own copy of the file), and fileName is the name to save it under.
function DownloadLink({ url, fileName, label }) {
  return (
    <a className="download" href={url} download={fileName ? fileName : undefined} title={label} aria-label={label}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 3v12" />       {/* the arrow's line */}
        <path d="M7 10l5 5 5-5" />  {/* the arrow's head */}
        <path d="M5 21h14" />       {/* the line under it */}
      </svg>
    </a>
  )
}

// Where the data file came from, in words
//   info:     api.dataFileInfo(): { origin, fileName, iri, ... }
//   location: the tss:dataFile IRI from the metadata store
function DataFileOrigin({ info, location }) {
  // The file's IRI as text: for a bundled endpoint the browser can't open it (data/ is not served)
  const iri = location ? <code>{location}</code> : <span>(the source has no tss:dataFile)</span>
  if (info !== null && info.origin === 'fetched') {
    return <>Downloaded by your browser from <a href={info.iri} target="_blank" rel="noreferrer">{info.iri}</a></>
  }
  if (info !== null && info.origin === 'picked') {
    return <>Picked from your computer: <strong>{info.fileName}</strong> (for the tss:dataFile {iri})</>
  }
  return iri
}

// Shows the data file of an external source: its first lines (a Parquet file is previewed as CSV
// text), with where the file is and a download icon. The api (dataApi.js) gives the preview
// ({ found, note, text }) and the download link, from the backend or from this browser tab.
//   api:      the endpoint's api object (see dataApi.js)
//   location: the tss:dataFile IRI from the metadata store, to show where the file is
export function FileViewer({ title, api, location, downloadLabel = 'Download the whole file' }) {
  const [file, setFile] = useState(null) // { found, note, text }
  const [info, setInfo] = useState(null) // { downloadUrl, fileName, origin, iri }
  const [error, setError] = useState(null)

  useEffect(() => {
    let ignore = false // see App.jsx: throw away an answer that arrives too late

    // Promise.all waits for both: the preview, and the download link (in the same order)
    Promise.all([api.dataPreview(), api.dataFileInfo()])
      .then((answers) => {
        if (ignore) return
        setFile(answers[0])
        setInfo(answers[1])
      })
      .catch((err) => {
        if (!ignore) setError(err.message)
      })

    return () => {
      ignore = true
    }
  }, [api])

  // First decide what goes inside the card...
  let content
  if (error) {
    content = <p className="error long-text">Error: {error}</p>
  } else if (file === null) {
    content = <p>Loading…</p>
  } else if (!file.found) {
    content = <p className="error long-text">{file.note} <DataFileOrigin info={info} location={location} /></p>
  } else {
    content = (
      <>
        <p className="muted small long-text">
          <DataFileOrigin info={info} location={location} /> {file.note && <span>({file.note})</span>}
          {info && <DownloadLink url={info.downloadUrl} fileName={info.fileName} label={downloadLabel} />}
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
