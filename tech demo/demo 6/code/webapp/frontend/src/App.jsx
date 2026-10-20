import { useState, useEffect } from 'react'
import { API_URL, errorText, fetchErrorText } from './config'
import { Overview } from './Overview'
import { Inspector } from './Inspector'
import { FileViewer } from './FileViewer'
import { Validation } from './Validation'
import { QueryForm } from './QueryForm'
import { OnlineEndpointForm } from './OnlineEndpointForm'
import { SqlBox } from './SqlBox'
import { Shacl } from './Shacl'
import { EmbeddedFiles } from './EmbeddedFiles'
import { RdfGeneration } from './RdfGeneration'

// The tabs of the dashboard: an id we use in the code, and the text on the button.
// updated: true when the tab already works with the new structure (tss:Source with tss:Field
// nodes). We update the tabs one by one; until then, a tab only shows a short note.
const tabs = [
  { id: 'overview', label: 'Overview', updated: true },
  { id: 'inspector', label: 'Series inspector', updated: true },
  { id: 'shacl', label: 'SHACL', updated: false },
  { id: 'rdf', label: 'RDF generation', updated: false },
  { id: 'files', label: 'Files', updated: false },
  { id: 'validation', label: 'CSVW validation', updated: false },
  { id: 'query', label: 'Query', updated: false },
  { id: 'sql', label: 'SQL (DuckDB)', updated: false },
]

// Is this tab already updated to the new structure?
function isUpdated(id) {
  return tabs.find((t) => t.id === id).updated
}

function App() {
  const [endpoints, setEndpoints] = useState(null) // the list for the drop down
  const [selectedId, setSelectedId] = useState('') // the endpoint the user picked
  const [info, setInfo] = useState(null)           // what the picked endpoint holds
  const [tab, setTab] = useState('overview')       // the tab that is open
  const [error, setError] = useState(null)

  // When the page opens: get the list of metadata endpoints
  useEffect(() => {
    fetch(`${API_URL}/endpoints`)
      .then((response) => response.json())
      .then((data) => {
        if (data.detail) setError(errorText(data.detail)) // FastAPI puts its error message in "detail"
        else setEndpoints(data)
      })
      .catch((err) => setError(err.message))
  }, [])

  // Every time selectedId changes: get what that endpoint holds
  useEffect(() => {
    if (selectedId === '') return

    // If the user picks another endpoint before this answer arrives, React first runs
    // the function we return at the bottom. That sets ignore = true, so the old,
    // late answer is thrown away instead of being shown for the new endpoint.
    let ignore = false

    fetch(`${API_URL}/endpoints/${selectedId}`)
      .then((response) => response.json())
      .then((data) => {
        if (ignore) return
        if (data.detail) setError(errorText(data.detail))
        else setInfo(data)
      })
      .catch((err) => {
        if (!ignore) setError(fetchErrorText(err))
      })

    return () => {
      ignore = true
    }
  }, [selectedId])

  function selectEndpoint(id) {
    // Same endpoint as before: nothing to do. (Without this line we would clear
    // info, but the useEffect would not run again to load it.)
    if (id === selectedId) return
    setSelectedId(id)
    setInfo(null) // forget the old endpoint while the new one loads
    setError(null)
  }

  function handleSelect(event) {
    selectEndpoint(event.target.value)
  }

  // An online endpoint was added: put it at the end of the drop down, and show it.
  // [...endpoints, endpoint] makes a new list: all the old endpoints plus the new one.
  function handleAdded(endpoint) {
    setEndpoints([...endpoints, endpoint])
    selectEndpoint(endpoint.id)
  }

  // Take the picked online endpoint out of the list again
  function removeSelected() {
    fetch(`${API_URL}/endpoints/${selectedId}`, { method: 'DELETE' })
      .then((response) => response.json())
      .then((data) => {
        if (data.detail) {
          setError(errorText(data.detail))
        } else {
          // filter makes a new list with every endpoint except the removed one
          setEndpoints(endpoints.filter((endpoint) => endpoint.id !== selectedId))
          selectEndpoint('')
        }
      })
      .catch((err) => setError(fetchErrorText(err)))
  }

  if (error && endpoints === null) {
    return (
      <p className="page error">
        Could not load the data sources from the backend at {API_URL}: {error}.
        "Failed to fetch" means the backend is not running (or not on port 8001): start it, then refresh this page.
      </p>
    )
  }
  if (endpoints === null) return <p className="page">Loading…</p>

  // The endpoint that is picked in the drop down (undefined when none is picked)
  const selected = endpoints.find((endpoint) => endpoint.id === selectedId)
  const parquet = info?.dataFormat === 'http://publications.europa.eu/resource/authority/file-type/PARQUET'

  return (
    <div className="page">
      <header className="header">
        <h1>TSS2 Dashboard</h1>
        <div className="source-picker">
          <label>
            Data source{' '}
            <select value={selectedId} onChange={handleSelect}>
              <option value="">-- pick a metadata endpoint --</option>
              {endpoints.map((endpoint) => (
                <option key={endpoint.id} value={endpoint.id}>
                  {endpoint.online ? endpoint.label + ' (online)' : endpoint.label}
                </option>
              ))}
            </select>
          </label>
          {selected && selected.online && (
            <button className="secondary" onClick={removeSelected} title={'Remove ' + selected.file + ' from the list'}>
              Remove
            </button>
          )}
        </div>
      </header>

      <OnlineEndpointForm onAdded={handleAdded} />

      {error && <p className="error long-text">Error: {error}</p>}

      {selectedId === '' && <p className="muted">Pick a data source in the drop down to start.</p>}

      {selectedId !== '' && info === null && !error && <p>Loading…</p>}

      {info !== null && (
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

          {!isUpdated(tab) && (
            <section className="card">
              <h3>{tabs.find((t) => t.id === tab).label}</h3>
              <p className="muted">
                This tab does not work with the new structure (<code>tss:Source</code> with <code>tss:Field</code> nodes) yet.
                We are updating the tabs one by one.
              </p>
            </section>
          )}

          {tab === 'overview' && <Overview info={info} />}

          {tab === 'inspector' && isUpdated('inspector') && <Inspector info={info} />}

          {isUpdated('shacl') && (
            <div style={{ display: tab === 'shacl' ? 'block' : 'none' }}>
              <Shacl key={info.id} info={info} />
            </div>
          )}

          {isUpdated('rdf') && (
            <div style={{ display: tab === 'rdf' ? 'block' : 'none' }}>
              <RdfGeneration key={info.id} info={info} />
            </div>
          )}

          {tab === 'files' && isUpdated('files') && info.embedded && <EmbeddedFiles info={info} />}
          {tab === 'files' && isUpdated('files') && !info.embedded && (
            <>
              <FileViewer
                key={`${info.id}-data`}
                title={parquet ? 'Data file (Parquet, previewed as CSV)' : 'Data file (tss:dataFile)'}
                url={`${API_URL}/endpoints/${info.id}/csv`}
                downloadUrl={`${API_URL}/endpoints/${info.id}/csv/download`}
                downloadLabel={parquet ? 'Download the original Parquet file' : 'Download the whole file'}
              />
              <FileViewer
                key={`${info.id}-csvw`}
                title="CSVW metadata file (tss:describedby)"
                url={`${API_URL}/endpoints/${info.id}/csvw`}
                downloadUrl={`${API_URL}/endpoints/${info.id}/csvw/download`}
              />
            </>
          )}

          {/* Validation stays on the page (only hidden), so a running validation and its
              result are not lost when you look at another tab in the meantime */}
          {isUpdated('validation') && (
            <div style={{ display: tab === 'validation' ? 'block' : 'none' }}>
              {info.csvwApplicable ? <Validation key={info.id} endpointId={info.id} /> : (
                <section className="card">
                  <h3>CSVW validation</h3>
                  <p className="muted">
                    Not applicable. CSVW validation is only available when the TSS declares{' '}
                    <code>dcterms:format filetype:CSV</code>.
                  </p>
                  {info.embedded && <p>Use the SHACL tab to check this endpoint's metadata and embedded point structure.</p>}
                </section>
              )}
            </div>
          )}

          {tab === 'query' && isUpdated('query') && <QueryForm info={info} />}

          {/* Like Validation: the SQL box stays on the page (only hidden), so your query
              and its result are still there when you come back to the tab */}
          {isUpdated('sql') && (
            <div style={{ display: tab === 'sql' ? 'block' : 'none' }}>
              <SqlBox info={info} />
            </div>
          )}
        </>
      )}
    </div>
  )
}

export default App
