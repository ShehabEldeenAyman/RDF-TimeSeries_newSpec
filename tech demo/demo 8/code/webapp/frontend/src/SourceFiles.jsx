import { useState, useEffect } from 'react'
import { writeTurtle } from './rdf'
import { fieldName, findField } from './fields'
import {
  has,
  one,
  oneText,
  readFields,
  isEmbedded,
  termText,
  RDFS_LABEL,
  TSS_COLUMN_NAME,
  TSS_DATA_FILE,
  TSS_DESCRIBES_FIELD,
  TSS_TIME_FIELD,
} from './tss'

// How many rows of the data the inspector shows
const ROW_COUNT = 10

// What the marks on the columns of the first rows mean
const BOUND_TEXT = {
  field: 'this field',
  time: 'time field',
  related: 'describes this field',
}

// What is behind the series inspector, so nothing is hidden: the RDF statements about the
// picked field and its source, and the first rows of the data.
//   endpoint, rdf, api: from the Inspector, which gets them from App.jsx (see the list of
//                       which props each tab gets there)
//   fieldIri:           the full IRI of the picked field
export function SourceFiles({ endpoint, rdf, api, fieldIri }) {
  const [view, setView] = useState('turtle')
  const [turtle, setTurtle] = useState(null) // { field, source }: two Turtle texts
  const [turtleError, setTurtleError] = useState(null)
  // The first rows of a CSV or Parquet file, from GET /rows: { columns: [field IRIs], rows: [[text or null]] }
  const [rows, setRows] = useState(null)
  const [rowsError, setRowsError] = useState(null)
  // Are the readings embedded in the metadata (nodes with tss:pointOf)? Then the rows come from rdf.points.
  const embedded = isEmbedded(rdf.store, rdf.source) === true

  // Every time another field is picked: write what the store says about the field, and
  // about its source, as Turtle. (These are the parsed triples, not the lines of the file:
  // the file itself is in the Files tab.)
  useEffect(() => {
    const field = findField(rdf.store, rdf.source, fieldIri)
    // Not a field of this source (should not happen): getQuads(null, ...) would give every quad
    if (field === null) return
    let ignore = false // see App.jsx: throw away an answer that arrives too late

    // Promise.all waits for both texts, and gives them in the same order
    Promise.all([
      writeTurtle(quadsAbout(rdf.store, field, []), rdf.prefixes),
      writeTurtle(quadsAbout(rdf.store, rdf.source, []), rdf.prefixes),
    ])
      .then((texts) => {
        if (!ignore) setTurtle({ field: texts[0], source: texts[1] })
      })
      .catch((err) => {
        if (!ignore) setTurtleError(err.message)
      })

    return () => {
      ignore = true
    }
  }, [rdf, fieldIri])

  // Once per endpoint: the first rows of the data, with each column named by the IRI of its field.
  // A CSV or Parquet file is read by the backend (GET /rows), or for your own endpoint here in
  // the browser: api.rows asks the right one (see dataApi.js).
  // (Embedded points are in the metadata itself: App.jsx already read them, see below.)
  useEffect(() => {
    if (embedded) return
    let ignore = false

    api.rows(ROW_COUNT)
      .then((data) => {
        if (!ignore) setRows(data)
      })
      .catch((err) => {
        if (!ignore) setRowsError(err.message)
      })

    return () => {
      ignore = true
    }
  }, [api, embedded])

  // Embedded points (tss:pointOf): App.jsx read them once (rdf.points), so the rows are made
  // here while drawing, by embeddedRows (see below). No state is needed for them: they are known
  // as soon as the tab is drawn.
  let shownRows = rows
  let shownError = rowsError
  if (embedded && rdf.points.error) shownError = "The points can't be shown as rows: " + rdf.points.error
  if (embedded && !rdf.points.error) shownRows = embeddedRows(rdf.points.rows, rdf)

  return (
    <section className="card">
      <div className="source-tabs">
        <button className={view === 'turtle' ? 'source-tab active' : 'source-tab'} onClick={() => setView('turtle')}>
          Turtle
        </button>
        <button className={view === 'rows' ? 'source-tab active' : 'source-tab'} onClick={() => setView('rows')}>
          First {shownRows !== null && shownRows.rows.length > 0 ? shownRows.rows.length + ' ' : ''}rows of the data
        </button>
      </div>

      {view === 'turtle' && <TurtleView endpoint={endpoint} turtle={turtle} error={turtleError} />}

      {view === 'rows' && <DataRows rdf={rdf} fieldIri={fieldIri} rows={shownRows} error={shownError} />}
    </section>
  )
}

// The first ROW_COUNT embedded points as table rows, in the same shape as the answer of
// /rows: { columns: [field IRIs], rows: [[text or null, ...], ...] }
//   pointRowList: the points as rows, in time order (rdf.points.rows: pointRows of the points
//                 App.jsx read)
//   columns: the fields with a tss:columnName, in the order of readFields, like the backend's
//            /rows. The other fields are shown as "not found".
//   rows:    each cell is the text of the field's literal, exactly as the document writes it,
//            or null when the point has no value.
function embeddedRows(pointRowList, rdf) {
  const columns = []
  for (const field of readFields(rdf.store, rdf.source)) {
    if (has(rdf.store, field, TSS_COLUMN_NAME, null)) columns.push(field.value)
  }

  const rows = []
  // slice(0, ROW_COUNT): only the first ROW_COUNT points
  for (const point of pointRowList.slice(0, ROW_COUNT)) {
    const cells = []
    for (const iri of columns) {
      // point.values has one term per field IRI; no value: no entry
      const value = point.values[iri]
      if (!value) cells.push(null)
      else if (value.termType === 'Literal') cells.push(value.value)
      else cells.push(termText(value, rdf.prefixes)) // a JSON object as a value is a blank node: _:b0
    }
    rows.push(cells)
  }
  return { columns: columns, rows: rows }
}

// The quads about a node, followed by those about the blank nodes it points to: a blank
// node has no IRI, so it is shown together with the node that uses it.
// seen: the blank nodes already added, so a loop of blank nodes can't go on forever.
function quadsAbout(store, node, seen) {
  const quads = store.getQuads(node, null, null, null)
  const result = quads.slice() // a copy, so we can add to it while we go through quads
  for (const quad of quads) {
    if (quad.object.termType === 'BlankNode' && !seen.includes(quad.object.value)) {
      seen.push(quad.object.value)
      for (const more of quadsAbout(store, quad.object, seen)) {
        result.push(more)
      }
    }
  }
  return result
}

// The statements about the field (blue) and about its source (yellow), as Turtle
function TurtleView({ endpoint, turtle, error }) {
  if (error) return <p className="error long-text">{error}</p>
  if (turtle === null) return <p>Loading…</p>

  return (
    <>
      <p className="muted small long-text">
        What <code>{endpoint.iri}</code> says about <span className="mark-field">this field</span> and about{' '}
        <span className="mark-source">its source</span>, written as Turtle from the parsed RDF.
        The whole document, exactly as it was sent, is in the Files tab.
      </p>
      <pre className="mark-field">{turtle.field}</pre>
      <pre className="mark-source">{turtle.source}</pre>
    </>
  )
}

// The first rows of the data as a table: a CSV or Parquet file, or the embedded points.
// The columns of this field, of the time field and of the fields that describe this one are coloured.
function DataRows({ rdf, fieldIri, rows, error }) {
  if (error) return <p className="error long-text">{error}</p>
  if (rows === null) return <p>Loading…</p>

  const store = rdf.store
  const source = rdf.source

  // Each column is named by its field's IRI (by the backend's /rows, or by embeddedRows).
  // The store says the rest: the column name (shown), the label (when you hover), and how
  // the column is marked.
  const columns = []
  for (const iri of rows.columns) {
    let bound = null
    if (iri === fieldIri) bound = 'field'
    else if (has(store, source, TSS_TIME_FIELD, iri)) bound = 'time'
    else if (has(store, iri, TSS_DESCRIBES_FIELD, fieldIri)) bound = 'related'
    const label = oneText(store, iri, RDFS_LABEL)
    columns.push({ name: fieldName(store, iri, rdf.prefixes), hover: label !== '' ? label + '\n' + iri : iri, bound: bound })
  }

  // The fields whose column is not in the data
  const notFound = []
  for (const field of readFields(store, source)) {
    if (!rows.columns.includes(field.value)) notFound.push(fieldName(store, field, rdf.prefixes))
  }

  // Where the rows come from: the data file (its IRI, as text: data/ is not served, so no
  // link), or the embedded points
  const dataFile = one(store, source, TSS_DATA_FILE)
  let where = <code>{dataFile ? dataFile.value : '(the source has no tss:dataFile)'}</code>
  if (isEmbedded(store, source) === true) {
    where = (
      <>
        the points (the nodes with <code>tss:pointOf</code>), read here in the browser from the metadata, in time
        order; each cell is the text of its literal, exactly as the document writes it
      </>
    )
  }

  return (
    <>
      <p className="muted small long-text">
        {where} · marked: <span className="mark-field">this field</span>{' '}
        <span className="mark-time">the time field</span>{' '}
        <span className="mark-related">fields that describe this one</span>
      </p>
      {notFound.length > 0 && <p className="error long-text">Not found in the data: {notFound.join(', ')}</p>}
      {rows.rows.length === 0 && <p className="muted">The data has no rows.</p>}
      <div className="table-scroll">
        <table className="data">
          <thead>
            <tr>
              {columns.map((column, index) => (
                <th key={index} className={column.bound ? 'mark-' + column.bound : ''} title={column.hover}>
                  {column.name}
                  {column.bound && <div className="small">{BOUND_TEXT[column.bound]}</div>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {row.map((cell, cellIndex) => {
                  const column = columns[cellIndex]
                  // A cell without a value is null: React shows nothing for it
                  return (
                    <td key={cellIndex} className={column && column.bound ? 'mark-' + column.bound : ''}>
                      {cell}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
