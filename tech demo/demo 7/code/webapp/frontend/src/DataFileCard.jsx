import { useState, useEffect } from 'react'

// For your own endpoint with a data file (tss:dataFile), above the tabs that need the data:
// where the browser got the file, or, when it could not get it, why, and a button to pick the
// file from your computer. (App.jsx shows it only on those tabs, so the file is only looked for
// when you open one of them.)
//   api:    the endpoint's data questions (dataApi.js); api.dataFileInfo() finds the file
//   onPick: called with the File you picked; App.jsx then makes a new api with it
export function DataFileCard({ api, onPick }) {
  const [info, setInfo] = useState(null)   // { downloadUrl, fileName, origin, iri }
  const [error, setError] = useState(null) // an Error; error.missingFile: the name of the file to pick

  useEffect(() => {
    let ignore = false // see App.jsx: throw away an answer that arrives too late
    api.dataFileInfo()
      .then((found) => {
        if (!ignore) setInfo(found)
      })
      .catch((err) => {
        if (!ignore) setError(err)
      })
    return () => {
      ignore = true
    }
  }, [api])

  function pick(event) {
    if (event.target.files.length > 0) onPick(event.target.files[0])
  }

  if (error) {
    return (
      <section className="card data-file-missing">
        <p className="error long-text">{error.message}</p>
        {error.missingFile && (
          <p>
            <label htmlFor="data-file-pick">Pick {error.missingFile} from your computer</label>{' '}
            <input id="data-file-pick" type="file" onChange={pick} />
          </p>
        )}
        <p className="muted small">The file you pick is read by this browser tab only, and kept in its memory.</p>
      </section>
    )
  }
  if (info === null) return <p className="muted small">Getting the data file…</p>

  return (
    <p className="muted small long-text">
      Data file:{' '}
      {info.origin === 'fetched'
        ? <>downloaded by your browser from <a href={info.iri} target="_blank" rel="noreferrer">{info.iri}</a></>
        : <>picked from your computer: <strong>{info.fileName}</strong></>}
      . It stays in this tab.
    </p>
  )
}
