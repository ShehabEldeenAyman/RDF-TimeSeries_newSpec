import { useState, useEffect, useMemo, useRef } from 'react'
import { API_URL } from './config'
import { loadRdf } from './rdf'
import { findSource, isEmbedded, isMutable, readCatalog, readPoints } from './tss'
import { makeDataApi } from './dataApi'
import { forgetDataFiles } from './browser/dataSource'
import { loadFromUrl, makeUserEndpoint, storedUrls, storeUrls } from './userEndpoints'
import { Overview } from './Overview'
import { Inspector } from './Inspector'
import { Files } from './Files'
import { QueryForm } from './QueryForm'
import { AddEndpointForm } from './AddEndpointForm'
import { DataFileCard } from './DataFileCard'
import { SqlBox } from './SqlBox'
import { Shacl } from './Shacl'
import { RdfGeneration } from './RdfGeneration'
import { AddPointForm } from './AddPointForm'

// App is the whole page. It does four things:
//   1. load the catalogue of the bundled metadata endpoints (an RDF document) and list them in a
//      drop down, followed by your own endpoints (uploaded or added by web address, kept in this tab)
//   2. load the metadata document of the endpoint you pick, and parse it into an RDF store
//      (and, for embedded points, read those as RDF too: once, here, for every tab)
//   3. make the endpoint's "api" (dataApi.js): the object every tab asks for the data. For a
//      bundled endpoint it asks the backend; for your own endpoint it computes everything here,
//      in the browser, so its data never goes to the server
//   4. show the tabs; every tab reads what it needs from that store and that api

// The tabs of the dashboard: an id we use in the code, the text on the button, and whether the
// tab needs the data itself (not only the metadata). "Add data" is only there for some endpoints
// (see canAddPoints).
const tabs = [
  { id: 'overview', label: 'Overview', data: false },
  { id: 'inspector', label: 'Series inspector', data: true },
  { id: 'shacl', label: 'SHACL', data: false },
  { id: 'rdf', label: 'RDF generation', data: true },
  { id: 'files', label: 'Files', data: true },
  { id: 'query', label: 'Query', data: true },
  { id: 'sql', label: 'SQL (DuckDB)', data: true },
  { id: 'add', label: 'Add data', data: false },
]

// The catalogue: the list of the bundled metadata endpoints, itself an RDF document (DCAT).
// The backend makes it from the files in "metadata endpoint/".
const CATALOG_URL = `${API_URL}/catalog`

// The text of an endpoint in the drop down: its label (the source's rdfs:label, copied
// into the catalogue), or its id when it has none. When the backend could not read the
// document, the catalogue says why (rdfs:comment) instead.
function optionText(endpoint) {
  let text = endpoint.label ? endpoint.label : endpoint.id
  if (endpoint.comment) text = endpoint.id + ' (' + endpoint.comment + ')'
  if (endpoint.user) text = text + ' (in this tab)'
  return text
}

// Does this tab show the data card of your own endpoint (DataFileCard)? Only a tab that needs
// the data, for an endpoint whose data is in a file (not embedded points).
function needsDataFile(tabId, endpoint, rdf) {
  const tab = tabs.find((t) => t.id === tabId)
  return endpoint.user === true && tab.data && isEmbedded(rdf.store, rdf.source) !== true
}

// Can points be added to this endpoint (the "Add data" tab)? Only to a bundled endpoint (the
// backend writes its file; your own endpoints are never changed) whose source says
// tss:embedded true (the points are in the metadata) and tss:mutable true (its data may change).
function canAddPoints(endpoint, rdf) {
  return endpoint.user !== true && isEmbedded(rdf.store, rdf.source) === true && isMutable(rdf.store, rdf.source) === true
}

function App() {
  const [endpoints, setEndpoints] = useState(null)       // the catalogue, as a list for the drop down
  const [userEndpoints, setUserEndpoints] = useState([]) // your own endpoints, in this tab (see userEndpoints.js)
  const [selected, setSelected] = useState(null)         // the endpoint the user picked (an entry of one of the lists)
  const [rdf, setRdf] = useState(null)                   // its metadata document, parsed into an RDF store
  const [tab, setTab] = useState('overview')             // the tab that is open
  const [opened, setOpened] = useState(['overview'])     // the tabs opened for this endpoint (see below)
  const [pickedFiles, setPickedFiles] = useState({})     // endpoint id -> the data file picked in a data tab:
                                                         // { file, anyway } (see handlePickedFile)
  const [error, setError] = useState(null)
  // The "Add data" tab saved a point: the endpoint's document is loaded again (reloadDocument).
  // reloads counts those loads (the document is loaded when it changes); lastAdded is the answer
  // of the save, for the tab to show after it is drawn again: { endpointId, answer }, or null.
  const [reloads, setReloads] = useState(0)
  const [lastAdded, setLastAdded] = useState(null)

  // Your endpoints added by web address before a reload of this tab (sessionStorage), read once
  // when the page opens. They are added again, one by one: restoring is true until then.
  const [restoreUrls] = useState(() => storedUrls())
  const [restoring, setRestoring] = useState(restoreUrls.length > 0)
  const [restoreProblems, setRestoreProblems] = useState([])
  // The number of the next user endpoint (user-1, user-2, ...). A ref: it changes without redrawing.
  const nextNumber = useRef(1)
  // Your endpoints as they are now. A function that waits for a download (the add form's, the
  // restore below) was made in an older redraw, and its userEndpoints is the list of back then;
  // this ref is always the list of now. Every change of the list sets both: the ref and the state
  // (addUserEndpoint, removeSelected).
  const userList = useRef([])

  // Your endpoint with this web address, if it is in the list already (an upload has none: null)
  function knownEndpoint(url) {
    if (url === null) return null
    const known = userList.current.find((entry) => entry.url === url)
    return known ? known : null
  }

  // Add one of your endpoints to the list, with the next number. Gives the new endpoint.
  function addUserEndpoint(document, how) {
    const endpoint = makeUserEndpoint(nextNumber.current, document, how)
    nextNumber.current += 1
    userList.current = userList.current.concat([endpoint])
    setUserEndpoints(userList.current)
    return endpoint
  }

  // When the page opens: load the catalogue and read the list of endpoints from it.
  // Each entry is { id, iri, downloadUrl, label, comment } (see readCatalog in tss.js).
  useEffect(() => {
    loadRdf(CATALOG_URL)
      .then((catalog) => setEndpoints(readCatalog(catalog.store)))
      .catch((err) => setError(err.message))
  }, [])

  // When the page opens after a reload: add your endpoints of before again (the browser downloads
  // them again). An address that does not work any more is left out, and the page says why.
  // An address you added with the form in the meantime is not added twice.
  useEffect(() => {
    if (restoreUrls.length === 0) return
    let ignore = false // see below: throw away answers that arrive too late
    const problems = []

    // One after the other (each .then waits for the one before), so they keep their order
    let chain = Promise.resolve()
    for (const url of restoreUrls) {
      chain = chain.then(() => {
        return loadFromUrl(url)
          .then((document) => {
            if (ignore) return
            if (knownEndpoint(url) !== null) return // added with the form while this one downloaded
            addUserEndpoint(document, { url: url, fileName: null, dataFiles: [] })
          })
          .catch((err) => {
            problems.push(`Could not add ${url} again: ${err.message}`)
          })
      })
    }
    chain.then(() => {
      if (ignore) return
      setRestoreProblems(problems)
      setRestoring(false)
    })

    return () => {
      ignore = true
    }
  }, [restoreUrls])

  // Remember the web addresses of your endpoints for a reload of this tab (not while the ones
  // of before are still being added: that would forget them)
  useEffect(() => {
    if (restoring) return
    const urls = []
    for (const endpoint of userEndpoints) {
      if (endpoint.url !== null) urls.push(endpoint.url)
    }
    storeUrls(urls)
  }, [userEndpoints, restoring])

  // Every time another endpoint is picked, or its document must be loaded again (reloads, after the
  // "Add data" tab saved a point): load its metadata document and parse it. Until the new one
  // arrives, the tabs keep showing the old one.
  useEffect(() => {
    if (selected === null) return

    // If the user picks another endpoint before this answer arrives, React first runs
    // the function we return at the bottom. That sets ignore = true, so the old,
    // late answer is thrown away instead of being shown for the new endpoint.
    let ignore = false

    // A bundled endpoint: fetch its document from our server.
    //   downloadUrl: where the browser fetches the document
    //   iri:         the document's own IRI. Relative IRIs in it, like <../data/x.csv>, are
    //                resolved against it, so they mean the same as for the document's author.
    //   cache: 'no-store': always ask the server, never the browser's cache. The "Add data" tab
    //                can change a bundled document, and an old copy kept by the browser would
    //                show the data of before (also when you come back to the endpoint later).
    // Your own endpoint: the browser already read the document when it was added.
    let loading
    if (selected.user) loading = Promise.resolve(selected.document)
    else loading = loadRdf(selected.downloadUrl, selected.iri, { cache: 'no-store' })

    loading
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
            text: loaded.text,         // the document as text, exactly as it was sent (or uploaded)
            url: loaded.url,           // where it was fetched from (null for an upload)
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
  }, [selected, reloads])

  // The api of the picked endpoint (see dataApi.js): made again when the endpoint, its document
  // or the data file picked for it changes. useMemo keeps the same object between redraws, so the
  // tabs don't ask their questions again on every redraw.
  const picked = selected !== null && pickedFiles[selected.id] ? pickedFiles[selected.id] : null
  const api = useMemo(() => {
    if (selected === null || rdf === null) return null
    return makeDataApi(selected, rdf, picked)
  }, [selected, rdf, picked])

  // When the api is replaced (another endpoint, or a picked data file) or the page closes:
  // close the old one. For your own endpoint that stops its DuckDB (about 400 MB of memory)
  // and frees its downloads (blob: addresses).
  useEffect(() => {
    if (api === null) return
    return () => api.close()
  }, [api])

  // Pick an endpoint: an entry of endpoints or userEndpoints, or null for none
  function selectEndpoint(endpoint) {
    // The same endpoint as before: nothing to do. (Without this line we would clear
    // rdf, but the useEffect would not run again to load it.)
    if (endpoint === selected) return
    setSelected(endpoint)
    setRdf(null)       // forget the old document while the new one loads
    // Only the open tab is drawn for the new endpoint (see openTab). "Add data" is not there
    // for every endpoint, so from there we go to the Overview.
    const nextTab = tab === 'add' ? 'overview' : tab
    setTab(nextTab)
    setOpened([nextTab])
    setLastAdded(null) // the answer of a save belongs to the endpoint it was made for
    setError(null)
  }

  // The drop down gives the id of the picked endpoint: find its entry in the lists
  // (find gives undefined for the "-- pick --" line, whose id is '')
  function handleSelect(event) {
    const all = endpoints.concat(userEndpoints)
    const endpoint = all.find((entry) => entry.id === event.target.value)
    selectEndpoint(endpoint ? endpoint : null)
  }

  // A tab is drawn the first time it is opened, and then kept (hidden when another tab is open),
  // so its results are still there when you come back. So nothing that needs the data (the data
  // file, DuckDB) starts before you open a tab that needs it.
  function openTab(id) {
    setTab(id)
    if (!opened.includes(id)) setOpened(opened.concat([id]))
  }

  // AddEndpointForm read a document: make it an endpoint of this tab, and pick it.
  // An address that is already in the list is only picked again. (knownEndpoint looks in the list
  // of now: the restore after a reload may have added it while the form was downloading.)
  function handleAdded(document, how) {
    const known = knownEndpoint(how.url)
    if (known !== null) {
      selectEndpoint(known)
      return
    }
    selectEndpoint(addUserEndpoint(document, how))
  }

  // Take the picked user endpoint out of the list. Its api is closed by the useEffect above
  // (the api becomes null); the data file the browser downloaded for it is forgotten here.
  function removeSelected() {
    const removed = selected
    userList.current = userList.current.filter((entry) => entry.id !== removed.id)
    setUserEndpoints(userList.current)
    forgetDataFiles(removed.id)
    const rest = Object.assign({}, pickedFiles)
    delete rest[removed.id]
    setPickedFiles(rest)
    selectEndpoint(null)
  }

  // The "Add data" tab saved a point in the endpoint's file: keep its answer (the tab shows it
  // again after it is drawn anew) and load the document again. The new document gives a new rdf,
  // and so a new api (useMemo above), and every tab is drawn again with the new data (key={api.key}).
  function reloadDocument(answer) {
    setLastAdded({ endpointId: selected.id, answer: answer })
    setReloads((count) => count + 1)
  }

  // A data file was chosen in a data tab (DataFileCard): keep it for this endpoint. The api is
  // made again with it, and the tabs start again with the new api (see key={api.key} below).
  //   anyway: true when you said "Use it anyway" for a file that does not fit the metadata
  //           (else the file is checked first, see browser/dataSource.js)
  function handlePickedFile(file, anyway) {
    setPickedFiles(Object.assign({}, pickedFiles, { [selected.id]: { file: file, anyway: anyway === true } }))
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
              {endpoints.concat(userEndpoints).map((endpoint) => (
                <option key={endpoint.id} value={endpoint.id}>
                  {optionText(endpoint)}
                </option>
              ))}
            </select>
          </label>
          {selected && selected.user && (
            <button className="secondary" onClick={removeSelected} title={'Remove ' + selected.iri + ' from this tab'}>
              Remove
            </button>
          )}
        </div>
      </header>

      <AddEndpointForm onAdded={handleAdded} />

      {restoring && <p className="muted small">Adding your endpoints of before the reload again…</p>}
      {restoreProblems.map((problem) => (
        <p key={problem} className="error small long-text">{problem}</p>
      ))}

      {error && <p className="error long-text">Error: {error}</p>}

      {selected === null && <p className="muted">Pick a data source in the drop down to start.</p>}

      {selected !== null && selected.user && (
        <p className="privacy-note">
          This endpoint and its data stay in this browser tab. Nothing is sent to the demo's server.
        </p>
      )}

      {selected !== null && rdf === null && !error && <p>Loading…</p>}

      {/* The props of the tabs (each tab gets only those it needs, see the list below):
            endpoint = { id, iri, label, ... }                              the endpoint (bundled, or your own: user true)
                       (Overview, Series inspector, Files)
            rdf      = { store, prefixes, text, url, source, points }       its parsed metadata document (every tab)
                       (points: the embedded points as RDF, or null; see readPoints in tss.js)
            api      = the endpoint's data questions (dataApi.js)           (every tab but Overview)
          The "Add data" tab also gets saved and onSaved (see reloadDocument).
          While another endpoint loads, rdf is null and all tabs disappear, so each tab
          starts fresh for every endpoint. */}
      {rdf !== null && api !== null && (
        <>
          <nav className="tabs">
            {tabs.filter((t) => t.id !== 'add' || canAddPoints(selected, rdf)).map((t) => (
              <button
                key={t.id}
                className={t.id === tab ? 'tab active' : 'tab'}
                onClick={() => openTab(t.id)}
              >
                {t.label}
              </button>
            ))}
          </nav>

          {/* key: a new api (a data file was picked) draws every tab again, from the start */}
          <div key={api.key}>
            {needsDataFile(tab, selected, rdf) && <DataFileCard api={api} onPick={handlePickedFile} />}

            {/* Only the tabs opened so far are drawn; the open one is shown, the others hidden */}
            {tabs.map((t) => {
              if (!opened.includes(t.id)) return null
              return (
                <div key={t.id} style={{ display: t.id === tab ? 'block' : 'none' }}>
                  {t.id === 'overview' && <Overview endpoint={selected} rdf={rdf} />}
                  {t.id === 'inspector' && <Inspector endpoint={selected} rdf={rdf} api={api} />}
                  {t.id === 'shacl' && <Shacl rdf={rdf} api={api} />}
                  {t.id === 'rdf' && <RdfGeneration rdf={rdf} api={api} />}
                  {t.id === 'files' && <Files endpoint={selected} rdf={rdf} api={api} />}
                  {t.id === 'query' && <QueryForm rdf={rdf} api={api} />}
                  {t.id === 'sql' && <SqlBox rdf={rdf} api={api} />}
                  {t.id === 'add' && canAddPoints(selected, rdf) && (
                    <AddPointForm
                      rdf={rdf}
                      api={api}
                      saved={lastAdded !== null && lastAdded.endpointId === selected.id ? lastAdded.answer : null}
                      onSaved={reloadDocument}
                    />
                  )}
                </div>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}

export default App
