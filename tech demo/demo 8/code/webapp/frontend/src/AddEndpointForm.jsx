import { useRef, useState } from 'react'
import { loadFromFile, loadFromUrl } from './userEndpoints'

// The form to add your own TSS2 metadata endpoint: an RDF document (Turtle, N-Triples, N3 or
// RDF/XML; not JSON-LD) with one tss:Source. Two ways:
//   - by web address: your browser downloads the document itself (the host must allow that: CORS)
//   - from your computer: pick the metadata file, and if you like its data files too
// Either way the browser reads it here, in this tab: nothing is sent to the demo's server.
//   onAdded(document, how): called with the parsed document and { url, fileName, dataFiles }
//                           (App.jsx makes the endpoint and picks it)
export function AddEndpointForm({ onAdded }) {
  const [url, setUrl] = useState('')
  const [metadataFile, setMetadataFile] = useState(null) // a File
  const [dataFiles, setDataFiles] = useState([])         // a list of Files
  const [publishedAt, setPublishedAt] = useState('')
  const [adding, setAdding] = useState(false)
  const [error, setError] = useState(null)
  // The file inputs, to empty them after an upload (a file input can't be set from state)
  const metadataInput = useRef(null)
  const dataInput = useRef(null)

  function addByUrl(event) {
    event.preventDefault() // stop the browser from reloading the page
    const address = url.trim()
    setAdding(true)
    setError(null)
    loadFromUrl(address)
      .then((document) => {
        setUrl('')
        onAdded(document, { url: address, fileName: null, dataFiles: [] })
      })
      .catch((err) => setError(err.message))
      .finally(() => setAdding(false))
  }

  function addByUpload(event) {
    event.preventDefault()
    setAdding(true)
    setError(null)
    loadFromFile(metadataFile, publishedAt.trim())
      .then((document) => {
        onAdded(document, { url: null, fileName: metadataFile.name, dataFiles: dataFiles })
        // Empty the form for the next one
        setMetadataFile(null)
        setDataFiles([])
        setPublishedAt('')
        if (metadataInput.current) metadataInput.current.value = ''
        if (dataInput.current) dataInput.current.value = ''
      })
      .catch((err) => setError(err.message))
      .finally(() => setAdding(false))
  }

  return (
    <details className="add-endpoint">
      <summary>Add your own endpoint (by web address, or from your computer)</summary>
      <p className="muted small">
        Your own endpoints stay in this browser tab: the browser reads the metadata and its data itself, and
        computes every tab here. Nothing is sent to the demo's server. The document must hold exactly one{' '}
        <code>tss:Source</code>, in Turtle, N-Triples, N3 or RDF/XML (not JSON-LD).
      </p>

      <form className="add-form" onSubmit={addByUrl}>
        <label htmlFor="user-url">By web address</label>
        <input
          id="user-url"
          type="url"
          required
          placeholder="https://example.org/metadata/my_series_tss2.ttl"
          value={url}
          onChange={(event) => setUrl(event.target.value)}
        />
        <button type="submit" disabled={adding}>{adding ? 'Adding…' : 'Add'}</button>
        <p className="muted small">
          Your browser downloads it (and its data file, when you open a data tab). The host must allow that (CORS):
          when it does not, download the files and add them from your computer below.
        </p>
      </form>

      <form className="add-form" onSubmit={addByUpload}>
        <label htmlFor="user-file">From your computer: the metadata file</label>
        <input
          ref={metadataInput}
          id="user-file"
          type="file"
          accept=".ttl,.nt,.n3,.rdf,.owl,.xml"
          onChange={(event) => setMetadataFile(event.target.files.length > 0 ? event.target.files[0] : null)}
        />
        <label htmlFor="user-data-files">its data files (optional)</label>
        {/* Array.from: files is a FileList, which is not a real array */}
        <input
          ref={dataInput}
          id="user-data-files"
          type="file"
          multiple
          onChange={(event) => setDataFiles(Array.from(event.target.files))}
        />
        <label htmlFor="user-published-at">published at (optional)</label>
        <input
          id="user-published-at"
          type="url"
          placeholder="https://example.org/metadata/my_series_tss2.ttl"
          value={publishedAt}
          onChange={(event) => setPublishedAt(event.target.value)}
        />
        <button type="submit" disabled={adding || metadataFile === null}>{adding ? 'Adding…' : 'Add'}</button>
        <p className="muted small">
          A data file is found by its name: the last part of the <code>tss:dataFile</code> IRI (upper or lower case
          does not matter; for an address that ends in a word like <code>/content</code> or <code>/download</code>, the
          part before it). A CSV file's header line is checked against the fields' column names before it is used. "Published at" is the
          document's address on the web, if it has one: relative IRIs in it, like <code>&lt;../data/x.csv&gt;</code>,
          are read against it (without it: <code>file:///uploaded/&lt;file name&gt;</code>). Uploaded files are kept
          in this tab's memory only: a reload of the page forgets them. Endpoints added by web address come back
          after a reload (the browser downloads them again).
        </p>
      </form>

      {error && <p className="error small long-text">{error}</p>}
    </details>
  )
}
