import { useState, useEffect } from 'react'
import { writeTurtle } from './rdf'
import { all, has, pointNodes, readFields, termText, TSS_HAS_FIELD, TSS_POINTS } from './tss'
import { fieldName } from './fields'

// The Files tab's viewer for the points embedded in the metadata: RDF nodes at the end of the
// document, each linked to its source with tss:pointOf. It shows them as a table with one column
// per field, and their triples as Turtle. App.jsx read the points once, for every tab (rdf.points,
// see readPoints in tss.js), so this viewer only shows them. (FileViewer.jsx shows an external
// data file instead.)

// How many values of the points have a property that is no field of the source (SHACL's P6
// warns about them; the tables leave them out)
//   points: the store of pointStore (the points' values, without their tss:pointOf)
function countOtherProperties(points, store, source) {
  let count = 0
  for (const quad of points.getQuads(null, null, null, null)) {
    if (!has(store, source, TSS_HAS_FIELD, quad.predicate)) count = count + 1
  }
  return count
}

// The points' triples as the document states them: every triple of a node with tss:pointOf the
// source, the tss:pointOf link included
function pointQuads(rdf) {
  const quads = []
  for (const point of pointNodes(rdf.store, rdf.source)) {
    for (const quad of rdf.store.getQuads(point, null, null, null)) quads.push(quad)
  }
  return quads
}

// Shows the embedded points of the source (see the top of this file).
//   rdf: the prop every tab gets (see App.jsx); rdf.points = { store, rows, error }
export function PointsViewer({ rdf }) {
  const points = rdf.points // the points, read once by App.jsx
  const [turtle, setTurtle] = useState(null) // the points' triples written as Turtle
  const [turtleError, setTurtleError] = useState(null)
  const tripleCount = pointQuads(rdf).length

  // Write the points' triples as Turtle, to show them under the table
  useEffect(() => {
    let ignore = false // see App.jsx: throw away an answer that arrives too late
    writeTurtle(pointQuads(rdf), rdf.prefixes)
      .then((text) => {
        if (!ignore) setTurtle(text)
      })
      .catch((err) => {
        if (!ignore) setTurtleError(err.message)
      })
    return () => {
      ignore = true
    }
  }, [rdf])

  // The old way of embedding points, a JSON literal on the source, is not read any more
  const oldPoints = all(rdf.store, rdf.source, TSS_POINTS)

  return (
    <section className="card">
      <h3>Embedded points (tss:pointOf)</h3>
      <p className="muted small">
        Each point is an RDF node at the end of the metadata document, linked to the source with tss:pointOf; each
        value is a triple with the field's IRI as property and a literal typed with the field's tss:datatype. Points
        are nodes and have no order, so the table sorts them by their tss:timeField value.
      </p>
      {oldPoints.length > 0 && (
        <p className="error small long-text">
          The source also has a tss:points literal (JSON). It is not read: the points are the nodes with tss:pointOf
          (the SHACL tab reports it as S12).
        </p>
      )}
      {points.error && <p className="error long-text">The points can't be shown as rows: {points.error}</p>}
      {turtleError && <p className="error long-text">The points can't be written as Turtle: {turtleError}</p>}
      {points.store !== null && <PointsTable rdf={rdf} points={points} />}
      {turtle !== null && (
        <details>
          <summary>Show the points as Turtle ({tripleCount} triples, with their tss:pointOf links)</summary>
          <pre>{turtle}</pre>
        </details>
      )}
    </section>
  )
}

// The points as a table: one row per point (sorted by time), one column per field.
// Each cell is the literal, written as in Turtle.
//   points: rdf.points, { store, rows, error }; rows are already in time order (pointRows)
function PointsTable({ rdf, points }) {
  const fields = readFields(rdf.store, rdf.source)
  const rows = points.rows
  const others = countOtherProperties(points.store, rdf.store, rdf.source)

  return (
    <>
      <p className="small">{rows.length} points.</p>
      {others > 0 && (
        <p className="error small">
          {others} values have a property that is no field of the source. They are not in this table (the SHACL tab
          warns about them: P6).
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
