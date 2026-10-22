import { useState } from 'react'
import { API_URL, errorText, fetchErrorText } from './config'
import { parseRdf } from './rdf'
import { readCatalog } from './tss'

// The backend answers with the new catalogue (Turtle), or with an error as JSON:
// { "detail": "..." }. This reads either answer and gives a Promise of the new list of
// endpoints. (App.jsx has the same function, for removing an endpoint.)
function readCatalogAnswer(response) {
  if (!response.ok) {
    const statusText = `${response.status} ${response.statusText}`
    return response
      .json()
      .catch(() => ({})) // the error was not JSON: use the status text
      .then((body) => {
        throw new Error(body.detail ? errorText(body.detail) : statusText)
      })
  }
  return response
    .text()
    // response.url is the full address we sent the request to: the base IRI for the Turtle
    .then((turtle) => parseRdf(turtle, 'text/turtle', response.url))
    .then((parsed) => readCatalog(parsed.store))
}

// A small form to add an online TSS2 metadata endpoint: the web address of an RDF document
// (Turtle, JSON-LD, ...) with one tss:Source. The backend downloads it, checks it, and
// answers with the new catalogue. From then on it works like the local ones: the data
// file it points to (tss:dataFile) is downloaded from the web as well.
//   onAdded(endpoints, added): called with the new list and the new entry in it
export function OnlineEndpointForm({ onAdded }) {
  const [url, setUrl] = useState('')
  const [adding, setAdding] = useState(false)
  const [error, setError] = useState(null)

  function handleSubmit(event) {
    event.preventDefault() // stop the browser from reloading the page
    setAdding(true)
    setError(null)

    // method: 'POST' because we ask the backend to add something (a normal fetch is a GET: only reading)
    const params = new URLSearchParams({ url: url })
    fetch(`${API_URL}/endpoints/online?${params}`, { method: 'POST' })
      // fetch itself fails only when there is no answer at all (the backend is not running)
      .catch((err) => {
        throw new Error(fetchErrorText(err))
      })
      .then((response) => readCatalogAnswer(response))
      .then((endpoints) => {
        // In the catalogue, an online endpoint's IRI (the dataset IRI) is the address we registered
        const added = endpoints.find((endpoint) => endpoint.iri === url)
        if (!added) {
          setError(`${url} was added, but it is not in the new catalogue under that address. Pick it in the drop down.`)
        }
        setUrl('')
        onAdded(endpoints, added ? added : null) // tell App: it shows the new list and picks the new endpoint
      })
      .catch((err) => setError(err.message))
      .finally(() => setAdding(false))
  }

  return (
    <form className="online-form" onSubmit={handleSubmit}>
      <label htmlFor="online-url">Add an online endpoint</label>
      <input
        id="online-url"
        type="url"
        required
        placeholder="https://example.org/metadata/my_series_tss2.ttl"
        value={url}
        onChange={(event) => setUrl(event.target.value)}
      />
      <button type="submit" disabled={adding}>
        {adding ? 'Adding…' : 'Add'}
      </button>
      {error && <p className="error small">{error}</p>}
    </form>
  )
}
