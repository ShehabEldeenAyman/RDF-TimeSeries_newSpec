import { useState, useEffect, useRef } from 'react'
import { DataFileOrigin } from './FileViewer'

// For your own endpoint with a data file (tss:dataFile), above the tabs that need the data:
//   - where the browser got the file, with a button to pick another one, or
//   - when it could not get the file, or the file does not fit the metadata: why, and what you
//     can do (pick a file from your computer, use the one data file you uploaded, or use a file
//     that does not fit anyway). See browser/dataSource.js.
// (App.jsx shows it only on those tabs, so the file is only looked for when you open one of them.)
//   api:    the endpoint's data questions (dataApi.js); api.dataFileInfo() finds the file
//   onPick: called with (file, anyway) when you choose a file: anyway is true for "Use it anyway".
//           App.jsx then makes a new api with it, and the file is checked again.
export function DataFileCard({ api, onPick }) {
  const [info, setInfo] = useState(null)   // { downloadUrl, fileName, origin, iri, note, warning }
  const [error, setError] = useState(null) // an Error; what it offers: see dataSource.js
  // The file input. It is hidden: the buttons below open it (a file input can't have a text of our own)
  const fileInput = useRef(null)

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

  // A button was clicked: open the browser's "choose a file" window
  function openPicker() {
    fileInput.current.click()
  }

  function pick(event) {
    if (event.target.files.length > 0) onPick(event.target.files[0], false)
  }

  const picker = <input ref={fileInput} id="data-file-pick" type="file" hidden onChange={pick} />

  if (error) {
    return (
      <section className="card data-file-missing">
        <p className="error long-text">{error.message}</p>
        <p className="data-file-actions">
          {/* the one data file you uploaded with the metadata, whose name matched nothing */}
          {error.offerFile && <button onClick={() => onPick(error.offerFile, false)}>Use {error.offerFile.name}</button>}
          {/* a file that does not fit the metadata: only when you say so */}
          {error.misfit && (
            <button className="secondary" onClick={() => onPick(error.misfit, true)}>Use {error.misfit.name} anyway</button>
          )}
          {error.missingFile && (
            <button className={error.offerFile ? 'secondary' : undefined} onClick={openPicker}>
              {error.misfit ? 'Pick another file' : `Pick ${error.missingFile} from your computer`}
            </button>
          )}
        </p>
        {picker}
        <p className="muted small">The file you pick is read by this browser tab only, and kept in its memory.</p>
      </section>
    )
  }
  if (info === null) return <p className="muted small">Getting the data file…</p>

  return (
    <div className="data-file-line">
      <p className="muted small long-text">
        <DataFileOrigin info={info} location={info.iri} />. {info.note && <>{info.note} </>}It stays in this tab.{' '}
        <button className="secondary" onClick={openPicker}>Pick another file</button>
      </p>
      {info.warning && <p className="error small long-text">{info.warning}</p>}
      {picker}
    </div>
  )
}
