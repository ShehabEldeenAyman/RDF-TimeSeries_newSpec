import { useState } from 'react'
import { API_URL } from './config'
import { FileViewer } from './FileViewer'

export function EmbeddedFiles({ info }) {
  const [snippet, setSnippet] = useState(info.snippets[0]?.id || '')
  return <>
    <section className="card">
      <h3>Embedded readings</h3>
      <label>Series{' '}<select value={snippet} onChange={(event) => setSnippet(event.target.value)}>
        {info.snippets.map((series) => <option key={series.id} value={series.id}>{series.label}</option>)}
      </select></label>
      <p className="muted small">The points are stored inside the Turtle file. Their JSON-LD context and observation metadata are available in the series inspector.</p>
    </section>
    {snippet && <FileViewer key={snippet} title="Points JSON (tss:points)" url={`${API_URL}/endpoints/${info.id}/points?${new URLSearchParams({ snippet })}`} />}
    <section className="card"><h3>Metadata Turtle</h3><pre>{info.turtle}</pre></section>
  </>
}
