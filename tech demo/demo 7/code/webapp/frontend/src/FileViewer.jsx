import { useState, useEffect } from 'react'
import { errorText, fetchErrorText } from './config'
import { writeTurtle } from './rdf'
import { all, readFields, shortName, termText, liftPoints, pointRows, RDF_JSON, TSS_POINTS, UNMAPPED } from './tss'
import { fieldName } from './fields'

// The two viewers of the Files tab (Files.jsx picks one):
//   FileViewer    an external data file (tss:dataFile): its first lines, from the backend
//   PointsViewer  the points embedded in the metadata (tss:points): the literal as it is
//                 written, and the same points read as RDF, in a table

// A small download icon. A normal link is enough here (no fetch):
// the backend answers with the file and tells the browser to save it.
function DownloadLink({ url, label }) {
  return (
    <a className="download" href={url} title={label} aria-label={label}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 3v12" />       {/* the arrow's line */}
        <path d="M7 10l5 5 5-5" />  {/* the arrow's head */}
        <path d="M5 21h14" />       {/* the line under it */}
      </svg>
    </a>
  )
}

// Shows the data file of an external source: its first lines, as text from the backend
// (GET /api/endpoints/{id}/data; a Parquet file is previewed as CSV text). The answer is
// JSON { found, note, text } with only the computed preview: where the file is comes
// from the metadata (location).
//   url          the /data route of the endpoint
//   location     the tss:dataFile IRI from the metadata store, to show where the file is
//   downloadUrl  optional: when it is given, the card shows a download icon
export function FileViewer({ title, url, location, downloadUrl, downloadLabel = 'Download the whole file' }) {
  const [file, setFile] = useState(null) // { found, note, text }
  const [error, setError] = useState(null)

  useEffect(() => {
    // An AbortController cancels the request when the card disappears before the answer arrives
    const controller = new AbortController()
    fetch(url, { signal: controller.signal })
      .then((response) => response.json())
      .then((data) => {
        if (data.detail) setError(errorText(data.detail)) // FastAPI puts its error message in "detail"
        else setFile(data)
      })
      .catch((err) => {
        if (err.name !== 'AbortError') setError(fetchErrorText(err))
      })
    return () => controller.abort()
  }, [url])

  // The file's IRI as text: the browser can't open it (data/ is not served), so no link
  const where = location ? <code>{location}</code> : <span>(the source has no tss:dataFile)</span>

  // First decide what goes inside the card...
  let content
  if (error) {
    content = <p className="error">Error: {error}</p>
  } else if (file === null) {
    content = <p>Loading…</p>
  } else if (!file.found) {
    content = <p className="error long-text">{file.note} {where}</p>
  } else {
    content = (
      <>
        <p className="muted small long-text">
          {where} {file.note && <span>({file.note})</span>}
          {downloadUrl && <DownloadLink url={downloadUrl} label={downloadLabel} />}
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

// What is wrong with the tss:points values, or null when there is exactly one rdf:JSON literal
function pointsProblem(points, prefixes) {
  if (points.length !== 1) return `The source has ${points.length} tss:points values; it needs exactly one.`
  const literal = points[0]
  if (literal.termType !== 'Literal') return `tss:points is ${termText(literal, prefixes)}, not a literal.`
  if (literal.datatype.value !== RDF_JSON) {
    return `tss:points has the datatype ${shortName(literal.datatype, prefixes)}, not rdf:JSON.`
  }
  return null
}

// How many values have a key that is no field's tss:columnName (their predicate is under UNMAPPED)
function countUnmapped(lifted) {
  let count = 0
  for (const quad of lifted.getQuads(null, null, null, null)) {
    if (quad.predicate.value.startsWith(UNMAPPED)) count = count + 1
  }
  return count
}

// Shows the points embedded in the metadata (tss:points), in two cards:
//   1. the rdf:JSON literal, exactly as it is written in the metadata
//   2. the points read as RDF (liftPoints in tss.js): a table with one column per field
export function PointsViewer({ rdf }) {
  const [lifted, setLifted] = useState(null) // the points as RDF: an N3 Store, one blank node per point
  const [turtle, setTurtle] = useState(null) // the same triples written as Turtle
  const [error, setError] = useState(null)

  useEffect(() => {
    let ignore = false // see App.jsx: throw away an answer that arrives too late
    liftPoints(rdf.store, rdf.source)
      .then((store) => {
        if (ignore) return
        setLifted(store)
        return writeTurtle(store.getQuads(null, null, null, null), rdf.prefixes).then((text) => {
          if (!ignore) setTurtle(text)
        })
      })
      .catch((err) => {
        if (!ignore) setError(err.message)
      })
    return () => {
      ignore = true
    }
  }, [rdf])

  const points = all(rdf.store, rdf.source, TSS_POINTS)
  const problem = pointsProblem(points, rdf.prefixes)

  return (
    <>
      <section className="card">
        <h3>Embedded points (tss:points)</h3>
        {problem ? (
          <p className="error long-text">{problem}</p>
        ) : (
          <>
            <p className="muted small">The rdf:JSON literal, exactly as it is in the metadata.</p>
            <pre>{points[0].value}</pre>
          </>
        )}
      </section>

      <section className="card">
        <h3>The points read as RDF</h3>
        {error && <p className="error long-text">The points can't be read as RDF: {error}</p>}
        {!error && lifted === null && <p>Reading the points…</p>}
        {lifted !== null && <PointsTable rdf={rdf} lifted={lifted} />}
        {turtle !== null && (
          <details>
            <summary>Show the triples as Turtle ({lifted.size} triples)</summary>
            <pre>{turtle}</pre>
          </details>
        )}
      </section>
    </>
  )
}

// The lifted points as a table: one row per point (sorted by time), one column per field.
// Each cell is the literal the JSON-LD parser made, written as in Turtle.
function PointsTable({ rdf, lifted }) {
  const fields = readFields(rdf.store, rdf.source)
  const rows = pointRows(lifted, rdf.store, rdf.source)
  const unmapped = countUnmapped(lifted)

  return (
    <>
      <p className="muted small">
        {rows.length} points. The JSON-LD context made from the fields turns each key (a tss:columnName) into the
        field's IRI and gives each value the field's tss:datatype. Points are blank nodes and have no order, so
        they are sorted by their tss:timeField value.
      </p>
      {unmapped > 0 && (
        <p className="error small">
          {unmapped} values have a key that is no field's tss:columnName. They are read with a predicate under{' '}
          <code>{UNMAPPED}</code> and are not in this table (the SHACL tab warns about them).
        </p>
      )}
      <div className="table-scroll">
        <table className="data">
          <thead>
            <tr>
              <th>#</th>
              {fields.map((field) => (
                <th key={field.value} title={field.value}>{fieldName(rdf.store, field, rdf.prefixes)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={index}>
                <td className="muted">{index + 1}</td>
                {fields.map((field) => {
                  // row.values has one literal per field IRI; no value: no entry
                  const value = row.values[field.value]
                  return <td key={field.value}>{value ? termText(value, rdf.prefixes) : <span className="muted">—</span>}</td>
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
