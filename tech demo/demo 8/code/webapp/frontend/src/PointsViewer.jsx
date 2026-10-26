import { useState, useEffect } from 'react'
import { writeTurtle } from './rdf'
import { all, readFields, shortName, termText, RDF_JSON, TSS_POINTS, UNMAPPED } from './tss'
import { fieldName } from './fields'

// The Files tab's viewer for the points embedded in the metadata (tss:points), in two cards:
//   1. the rdf:JSON literal, exactly as it is written in the metadata
//   2. the points read as RDF: a table with one column per field, and the triples as Turtle
// App.jsx read the points as RDF once, for every tab (rdf.points, see readPoints in tss.js),
// so this viewer only shows them. (FileViewer.jsx shows an external data file instead.)

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

// Shows the embedded points of the source in two cards (see the top of this file).
//   rdf: the prop every tab gets (see App.jsx); rdf.points = { store, rows, error }
export function PointsViewer({ rdf }) {
  const points = rdf.points // the points as RDF, read once by App.jsx
  const [turtle, setTurtle] = useState(null) // the lifted triples written as Turtle
  const [turtleError, setTurtleError] = useState(null)

  // Write the lifted triples as Turtle, to show them under the table
  useEffect(() => {
    const lifted = rdf.points.store
    if (lifted === null) return // the points can't be read: card 2 says why
    let ignore = false // see App.jsx: throw away an answer that arrives too late
    writeTurtle(lifted.getQuads(null, null, null, null), rdf.prefixes)
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

  // The tss:points values in the metadata (normally one rdf:JSON literal)
  const literals = all(rdf.store, rdf.source, TSS_POINTS)
  const problem = pointsProblem(literals, rdf.prefixes)

  return (
    <>
      <section className="card">
        <h3>Embedded points (tss:points)</h3>
        {problem ? (
          <p className="error long-text">{problem}</p>
        ) : (
          <>
            <p className="muted small">The rdf:JSON literal, exactly as it is in the metadata.</p>
            <pre>{literals[0].value}</pre>
          </>
        )}
      </section>

      <section className="card">
        <h3>The points read as RDF</h3>
        {points.error && <p className="error long-text">The points can't be read as RDF: {points.error}</p>}
        {turtleError && <p className="error long-text">The points can't be read as RDF: {turtleError}</p>}
        {points.store !== null && <PointsTable rdf={rdf} points={points} />}
        {turtle !== null && (
          <details>
            <summary>Show the triples as Turtle ({points.store.size} triples)</summary>
            <pre>{turtle}</pre>
          </details>
        )}
      </section>
    </>
  )
}

// The lifted points as a table: one row per point (sorted by time), one column per field.
// Each cell is the literal the JSON-LD parser made, written as in Turtle.
//   points: rdf.points, { store, rows, error }; rows are already in time order (pointRows)
function PointsTable({ rdf, points }) {
  const fields = readFields(rdf.store, rdf.source)
  const rows = points.rows
  const unmapped = countUnmapped(points.store)

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
