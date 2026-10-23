import { useState, useEffect } from 'react'
import { API_URL, fetchErrorText } from './config'
import { loadRdf, readRdfAnswer } from './rdf'
import { findSource, readCatalog, readPoints } from './tss'
import { Overview } from './Overview'
import { Inspector } from './Inspector'
import { Files } from './Files'
import { QueryForm } from './QueryForm'
import { OnlineEndpointForm } from './OnlineEndpointForm'
import { SqlBox } from './SqlBox'
import { Shacl } from './Shacl'
import { RdfGeneration } from './RdfGeneration'

// App is the whole page. It does three things:
//   1. load the catalogue of metadata endpoints (an RDF document) and list them in a drop down
//   2. load the metadata document of the endpoint you pick, and parse it into an RDF store
//      (and, for embedded points, read those as RDF too: once, here, for every tab)
//   3. show the tabs; every tab reads what it needs from that store

// The tabs of the dashboard: an id we use in the code, and the text on the button
const tabs = [
  { id: 'overview', label: 'Overview' },
  { id: 'inspector', label: 'Series inspector' },
  { id: 'shacl', label: 'SHACL' },
  { id: 'rdf', label: 'RDF generation' },
  { id: 'files', label: 'Files' },
  { id: 'query', label: 'Query' },
  { id: 'sql', label: 'SQL (DuckDB)' },
]

// The catalogue: the list of metadata endpoints, itself an RDF document (DCAT).
// The backend makes it from the files in "metadata endpoint/" and the online endpoints you added.
const CATALOG_URL = `${API_URL}/catalog`

// The text of an endpoint in the drop down: its label (the source's rdfs:label, copied
// into the catalogue), or its id when it has none. When the backend could not read the
// document, the catalogue says why (rdfs:comment) instead.
function optionText(endpoint) {
  let text = endpoint.label ? endpoint.label : endpoint.id
  if (endpoint.comment) text = endpoint.id + ' (' + endpoint.comment + ')'
  if (endpoint.online) text = text + ' (online)'
  return text
}

function App() {
  const [endpoints, setEndpoints] = useState(null) // the catalogue, as a list for the drop down
  const [selected, setSelected] = useState(null)   // the endpoint the user picked (one entry of endpoints)
  const [rdf, setRdf] = useState(null)             // its metadata document, parsed into an RDF store
  const [tab, setTab] = useState('overview')       // the tab that is open
  const [error, setError] = useState(null)

  // When the page opens: load the catalogue and read the list of endpoints from it.
  // Each entry is { id, iri, downloadUrl, label, comment, online } (see readCatalog in tss.js).
  useEffect(() => {
    loadRdf(CATALOG_URL)
      .then((catalog) => setEndpoints(readCatalog(catalog.store)))
      .catch((err) => setError(err.message))
  }, [])

  // Every time another endpoint is picked: load its metadata document and parse it
  useEffect(() => {
    if (selected === null) return

    // If the user picks another endpoint before this answer arrives, React first runs
    // the function we return at the bottom. That sets ignore = true, so the old,
    // late answer is thrown away instead of being shown for the new endpoint.
    let ignore = false

    // downloadUrl: where the browser fetches the document (an online one comes through our backend)
    // iri:         the document's own IRI. Relative IRIs in it, like <../data/x.csv>, are
    //              resolved against it, so they mean the same as for the document's author.
    loadRdf(selected.downloadUrl, selected.iri)
      .then((loaded) => {
        if (ignore) return
        // Every tab is about the one tss:Source of the document: find it once, here
        const source = findSource(loaded.store)
        if (source === null) {
          setError(`${selected.iri} has no tss:Source, or more than one. This app needs exactly one.`)
          return
        }
        // The embedded points (tss:points) are read as RDF once, here, for every tab
        // (see readPoints in tss.js). Its Promise is never rejected: a problem with the
        // points is part of the answer, and the tabs show it.
        return readPoints(loaded.store, source).then((points) => {
          if (ignore) return
          // What every tab gets as its "rdf" prop
          setRdf({
            store: loaded.store,       // all the triples, in an N3 Store (ask it with the helpers in tss.js)
            prefixes: loaded.prefixes, // the document's @prefix lines, to show IRIs short (tss:CSV)
            text: loaded.text,         // the document as text, exactly as it was sent
            url: loaded.url,           // where it was fetched from
            source: source,            // the tss:Source (a NamedNode)
            points: points,            // the embedded points read as RDF: null when the source is not
                                       // embedded, else { store, rows, error } (see readPoints)
          })
        })
      })
      .catch((err) => {
        if (!ignore) setError(err.message)
      })

    return () => {
      ignore = true
    }
  }, [selected])

  // Pick an endpoint: an entry of endpoints, or null for none
  function selectEndpoint(endpoint) {
    // The same endpoint as before: nothing to do. (Without this line we would clear
    // rdf, but the useEffect would not run again to load it.)
    if (endpoint === selected) return
    setSelected(endpoint)
    setRdf(null) // forget the old document while the new one loads
    setError(null)
  }

  // The drop down gives the id of the picked endpoint: find its entry in the list
  // (find gives undefined for the "-- pick --" line, whose id is '')
  function handleSelect(event) {
    const endpoint = endpoints.find((entry) => entry.id === event.target.value)
    selectEndpoint(endpoint ? endpoint : null)
  }

  // OnlineEndpointForm added an endpoint: show the new catalogue and pick the new entry.
  // (added is null when the form could not find it in the new catalogue: then the
  // endpoint you were looking at stays open.)
  function handleAdded(newEndpoints, added) {
    setEndpoints(newEndpoints)
    if (added !== null) selectEndpoint(added)
  }

  // Take the picked online endpoint out of the catalogue again
  function removeSelected() {
    // method: 'DELETE' asks the backend to remove something; it answers with the new catalogue
    fetch(`${API_URL}/endpoints/${encodeURIComponent(selected.id)}`, { method: 'DELETE' })
      // fetch itself fails only when there is no answer at all (the backend is not running)
      .catch((err) => {
        throw new Error(fetchErrorText(err))
      })
      // The answer is the new catalogue (Turtle), or an error as JSON (see readRdfAnswer in rdf.js).
      // response.url is the full address we sent the request to: the base IRI for the Turtle.
      .then((response) => readRdfAnswer(response, response.url))
      .then((parsed) => readCatalog(parsed.store))
      .then((newEndpoints) => {
        setEndpoints(newEndpoints)
        selectEndpoint(null)
      })
      .catch((err) => setError(err.message))
  }

  if (error && endpoints === null) {
    return (
      <p className="page error long-text">
        Could not load the catalogue of metadata endpoints. {error} (The backend runs on port 8001:
        start it if it is not running, then refresh this page.)
      </p>
    )
  }
  if (endpoints === null) return <p className="page">Loading…</p>

  return (
    <div className="page">
      <header className="header">
        <h1>TSS2 Dashboard</h1>
        <div className="source-picker">
          <label>
            Data source{' '}
            <select value={selected ? selected.id : ''} onChange={handleSelect}>
              <option value="">-- pick a metadata endpoint --</option>
              {endpoints.map((endpoint) => (
                <option key={endpoint.id} value={endpoint.id}>
                  {optionText(endpoint)}
                </option>
              ))}
            </select>
          </label>
          {selected && selected.online && (
            <button className="secondary" onClick={removeSelected} title={'Remove ' + selected.iri + ' from the list'}>
              Remove
            </button>
          )}
        </div>
      </header>

      <OnlineEndpointForm onAdded={handleAdded} />

      {error && <p className="error long-text">Error: {error}</p>}

      {selected === null && <p className="muted">Pick a data source in the drop down to start.</p>}

      {selected !== null && rdf === null && !error && <p>Loading…</p>}

      {/* Every tab gets the same two props:
            endpoint = { id, iri, downloadUrl, label, online }          the entry from the catalogue
            rdf      = { store, prefixes, text, url, source, points }   its parsed metadata document
                       (points: the embedded points as RDF, or null; see readPoints in tss.js)
          While another endpoint loads, rdf is null and all tabs disappear, so each tab
          starts fresh for every endpoint. */}
      {rdf !== null && (
        <>
          <nav className="tabs">
            {tabs.map((t) => (
              <button
                key={t.id}
                className={t.id === tab ? 'tab active' : 'tab'}
                onClick={() => setTab(t.id)}
              >
                {t.label}
              </button>
            ))}
          </nav>

          {tab === 'overview' && <Overview endpoint={selected} rdf={rdf} />}

          {tab === 'inspector' && <Inspector endpoint={selected} rdf={rdf} />}

          {/* SHACL, RDF generation and SQL stay on the page (only hidden), so a result
              is still there when you come back to the tab */}
          <div style={{ display: tab === 'shacl' ? 'block' : 'none' }}>
            <Shacl endpoint={selected} rdf={rdf} />
          </div>

          <div style={{ display: tab === 'rdf' ? 'block' : 'none' }}>
            <RdfGeneration endpoint={selected} rdf={rdf} />
          </div>

          {tab === 'files' && <Files endpoint={selected} rdf={rdf} />}

          {tab === 'query' && <QueryForm endpoint={selected} rdf={rdf} />}

          <div style={{ display: tab === 'sql' ? 'block' : 'none' }}>
            <SqlBox endpoint={selected} rdf={rdf} />
          </div>
        </>
      )}
    </div>
  )
}

export default App
