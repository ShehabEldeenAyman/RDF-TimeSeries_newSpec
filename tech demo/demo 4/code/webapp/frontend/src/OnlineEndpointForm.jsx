import { useState } from 'react'
import { API_URL, errorText, fetchErrorText } from './config'

// A small form to add an online TSS2 metadata endpoint: the web address of a Turtle file.
// The backend downloads it and checks it. From then on it works like the local ones:
// the CSV and CSVW files it points to are downloaded from the web as well.
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
      .then((response) => response.json())
      .then((data) => {
        if (data.detail) {
          setError(errorText(data.detail))
        } else {
          setUrl('')
          onAdded(data) // tell App, so it can put the new endpoint in the drop down and show it
        }
      })
      .catch((err) => setError(fetchErrorText(err)))
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
