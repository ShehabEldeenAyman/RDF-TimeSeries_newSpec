import { roleText } from './fields'
import {
  all,
  has,
  one,
  readFields,
  isEmbedded,
  shortName,
  termText,
  RDF_TYPE,
  RDFS_LABEL,
  RDFS_COMMENT,
  TSS_FIELD,
  TSS_EMBEDDED,
  TSS_FORMAT,
  TSS_CSV,
  TSS_PARQUET,
  TSS_DATA_FILE,
  TSS_MUTABLE,
  TSS_PRODUCED_BY,
  TSS_FROM,
  TSS_UNTIL,
  TSS_TIME_FIELD,
  TSS_KEY,
  TSS_HAS_FIELD,
  TSS_POINTS,
  TSS_DELIMITER,
  TSS_HEADER,
  TSS_ENCODING,
  TSS_COLUMN_NAME,
  TSS_COLUMN_INDEX,
  TSS_DATATYPE,
  TSS_LANGUAGE,
  TSS_UNIT,
  TSS_OBSERVED_PROPERTY,
  TSS_SENSOR,
  TSS_FEATURE_OF_INTEREST,
  TSS_MINIMUM,
  TSS_MAXIMUM,
  TSS_DESCRIBES_FIELD,
} from './tss'

// The properties of a field that the fields table shows. Everything else the document
// says is listed under "Other statements", so nothing in the metadata is hidden.
const FIELD_PROPERTIES = [
  TSS_COLUMN_NAME,
  TSS_COLUMN_INDEX,
  RDFS_LABEL,
  RDFS_COMMENT,
  TSS_DATATYPE,
  TSS_LANGUAGE,
  TSS_UNIT,
  TSS_OBSERVED_PROPERTY,
  TSS_SENSOR,
  TSS_FEATURE_OF_INTEREST,
  TSS_MINIMUM,
  TSS_MAXIMUM,
  TSS_DESCRIBES_FIELD, // in the Role column
]

// One RDF term as text for people:
//   an IRI        its short name, e.g. tss:CSV (hover over it: the full IRI)
//   a literal     as in Turtle, with its datatype: "2021-03-03T23:15:00Z"^^xsd:dateTime, ";"
//                 plain: only its text, e.g. 10000 (hover over it: the literal as in Turtle)
//   a blank node  _:b0
function Term({ term, prefixes, plain }) {
  if (term.termType === 'NamedNode') return <span title={term.value}>{shortName(term, prefixes)}</span>
  if (term.termType === 'Literal' && plain) return <span title={termText(term, prefixes)}>{term.value}</span>
  return <span>{termText(term, prefixes)}</span>
}

// Several terms (all values of one property), separated by commas. A dash when there are none.
function Terms({ terms, prefixes, plain }) {
  if (terms.length === 0) return '—'
  return terms.map((term, index) => (
    <span key={index}>
      {index > 0 && ', '}
      <Term term={term} prefixes={prefixes} plain={plain} />
    </span>
  ))
}

// Like Terms, but a web IRI is a link to itself (a unit IRI opens the QUDT page of that unit)
function Links({ terms, prefixes }) {
  if (terms.length === 0) return '—'
  return terms.map((term, index) => {
    const isWebIri = term.termType === 'NamedNode' && (term.value.startsWith('http://') || term.value.startsWith('https://'))
    return (
      <span key={index}>
        {index > 0 && ', '}
        {isWebIri ? (
          <a href={term.value} title={term.value} target="_blank" rel="noreferrer">{shortName(term, prefixes)}</a>
        ) : (
          <Term term={term} prefixes={prefixes} />
        )}
      </span>
    )
  })
}

// One row of a facts table: one property of the source, with all its values. The triple
//   ex:grootdok-source  tss:from  "2021-03-03T23:45:00Z"^^xsd:dateTime .
// becomes the row  | tss:from | "2021-03-03T23:45:00Z"^^xsd:dateTime |
function PropertyRow({ rdf, property }) {
  return (
    <tr>
      <th title={property}>{shortName(property, rdf.prefixes)}</th>
      <td>
        <Terms terms={all(rdf.store, rdf.source, property)} prefixes={rdf.prefixes} />
      </td>
    </tr>
  )
}

// The allowed values of a field (tss:minimum, tss:maximum), e.g. "0 to 10000". "…" when one side is missing.
function Range({ rdf, field }) {
  const minimums = all(rdf.store, field, TSS_MINIMUM)
  const maximums = all(rdf.store, field, TSS_MAXIMUM)
  if (minimums.length === 0 && maximums.length === 0) return '—'
  return (
    <>
      {minimums.length > 0 ? <Terms terms={minimums} prefixes={rdf.prefixes} plain /> : '…'}
      {' to '}
      {maximums.length > 0 ? <Terms terms={maximums} prefixes={rdf.prefixes} plain /> : '…'}
    </>
  )
}

// One row of the fields table: what one field says about its column
function FieldRow({ rdf, field, hasIndexes }) {
  const store = rdf.store
  const prefixes = rdf.prefixes
  const language = one(store, field, TSS_LANGUAGE) // only for text with a language tag (rdf:langString)

  return (
    <tr>
      <td>
        <code><Terms terms={all(store, field, TSS_COLUMN_NAME)} prefixes={prefixes} plain /></code>
        <div className="muted small" title={field.value}>{shortName(field, prefixes)}</div>
      </td>
      {hasIndexes && <td><Terms terms={all(store, field, TSS_COLUMN_INDEX)} prefixes={prefixes} plain /></td>}
      <td>{roleText(store, rdf.source, field, prefixes)}</td>
      <td>
        {all(store, field, RDFS_LABEL).map((label, index) => (
          <div key={index}>{label.value}</div>
        ))}
        {all(store, field, RDFS_COMMENT).map((comment, index) => (
          <div key={index} className="muted small">{comment.value}</div>
        ))}
      </td>
      <td>
        <Terms terms={all(store, field, TSS_DATATYPE)} prefixes={prefixes} />
        {language && <span className="muted small"> ({language.value})</span>}
      </td>
      <td><Links terms={all(store, field, TSS_UNIT)} prefixes={prefixes} /></td>
      <td><Terms terms={all(store, field, TSS_OBSERVED_PROPERTY)} prefixes={prefixes} /></td>
      <td><Terms terms={all(store, field, TSS_SENSOR)} prefixes={prefixes} /></td>
      <td><Terms terms={all(store, field, TSS_FEATURE_OF_INTEREST)} prefixes={prefixes} /></td>
      <td><Range rdf={rdf} field={field} /></td>
    </tr>
  )
}

// For embedded points: how the app reads tss:points, and how many points that gives
//   points: rdf.points, the points App.jsx read as RDF: { store, rows, error } (see readPoints in tss.js)
function PointsNote({ points }) {
  return (
    <>
      <p className="muted small">
        tss:points is read as JSON-LD, with a context made from the fields: a key (tss:columnName) becomes the
        field's IRI, its value gets the field's tss:datatype.
      </p>
      {points.error && <p className="error small long-text">The points can't be read as RDF: {points.error}</p>}
      {!points.error && <p className="small">{points.rows.length} points, one blank node each (the Files tab shows them as a table).</p>}
    </>
  )
}

// Is this statement (quad) one of those the cards of the Overview show?
//   shownSourceProperties: the properties of the source the "Source" and "Reading the data" cards show
function isShown(quad, source, fields, shownSourceProperties) {
  const predicate = quad.predicate.value
  if (quad.subject.equals(source)) {
    return shownSourceProperties.includes(predicate)
  }
  // Is the subject one of the fields of the source? (equals compares two RDF terms)
  const isField = fields.some((field) => field.equals(quad.subject))
  if (!isField) return false
  // "a tss:Field" is what every field is: no need to list it
  if (predicate === RDF_TYPE && quad.object.value === TSS_FIELD) return true
  return FIELD_PROPERTIES.includes(predicate)
}

// All statements of the document that the cards do not show: other properties of the
// source and its fields (a licence, provenance, ...), and what the document says about
// anything else (a sensor, a station, ...)
function otherStatements(store, source, fields, shownSourceProperties) {
  const others = []
  for (const quad of store.getQuads(null, null, null, null)) {
    if (!isShown(quad, source, fields, shownSourceProperties)) others.push(quad)
  }
  return others
}

// Everything one metadata endpoint says, read from its RDF store.
//   endpoint: the catalogue entry ({ id, iri, online, ... })
//   rdf:      the parsed document ({ store, prefixes, text, source, points }); rdf.source is its tss:Source
export function Overview({ endpoint, rdf }) {
  const store = rdf.store
  const source = rdf.source
  const prefixes = rdf.prefixes

  const embedded = isEmbedded(store, source)   // true, false, or null (not stated)
  const format = one(store, source, TSS_FORMAT) // e.g. the NamedNode tss:CSV, or null
  // Compare the full IRIs: the document may use any prefix for tss:, or none at all
  const csv = embedded !== true && format !== null && format.value === TSS_CSV
  const parquet = embedded !== true && format !== null && format.value === TSS_PARQUET
  const fields = readFields(store, source)
  const labels = all(store, source, RDFS_LABEL)

  // Only show the "Index" column when at least one field has a tss:columnIndex
  const hasIndexes = fields.some((field) => has(store, field, TSS_COLUMN_INDEX, null))

  // The properties of the source that the "Source" and "Reading the data" cards show
  // (tss:hasField: the fields table; tss:points: the points note and the Files tab)
  const shownSourceProperties = [
    RDF_TYPE, RDFS_LABEL, TSS_EMBEDDED, TSS_MUTABLE, TSS_PRODUCED_BY,
    TSS_FROM, TSS_UNTIL, TSS_TIME_FIELD, TSS_KEY, TSS_HAS_FIELD,
  ]
  if (embedded === true) shownSourceProperties.push(TSS_POINTS)
  else shownSourceProperties.push(TSS_FORMAT, TSS_DATA_FILE)
  if (csv) shownSourceProperties.push(TSS_DELIMITER, TSS_HEADER, TSS_ENCODING)
  const others = otherStatements(store, source, fields, shownSourceProperties)

  return (
    <>
      <section className="card">
        <h2>{labels.length > 0 ? labels.map((label) => label.value).join(' / ') : endpoint.id}</h2>
        <p className="muted long-text">
          {endpoint.online ? 'Online metadata endpoint: ' : 'Metadata endpoint file: '}
          <a href={endpoint.iri} target="_blank" rel="noreferrer">{endpoint.iri}</a>
        </p>
      </section>

      <div className="two-columns">
        <section className="card">
          <h3>Source</h3>
          <p className="muted small">
            {embedded === true && 'The readings are embedded in the metadata, in tss:points.'}
            {embedded === false && 'The readings are in an external data file (tss:dataFile).'}
            {embedded === null && 'tss:embedded is not stated as true or false, so it is not clear where the readings are.'}
          </p>
          <table className="facts">
            <tbody>
              <tr>
                <th>IRI</th>
                <td>{source.value}</td>
              </tr>
              <PropertyRow rdf={rdf} property={RDF_TYPE} />
              <PropertyRow rdf={rdf} property={TSS_EMBEDDED} />
              {embedded !== true && <PropertyRow rdf={rdf} property={TSS_FORMAT} />}
              {embedded !== true && <PropertyRow rdf={rdf} property={TSS_DATA_FILE} />}
              <PropertyRow rdf={rdf} property={TSS_MUTABLE} />
              <PropertyRow rdf={rdf} property={TSS_PRODUCED_BY} />
              <PropertyRow rdf={rdf} property={TSS_FROM} />
              <PropertyRow rdf={rdf} property={TSS_UNTIL} />
              <PropertyRow rdf={rdf} property={TSS_TIME_FIELD} />
              <PropertyRow rdf={rdf} property={TSS_KEY} />
            </tbody>
          </table>
          <p className="muted small">
            The readings lie in the window [tss:from, tss:until): from is included, until is not.
            {embedded !== true && ' The tss:dataFile IRI is resolved against the IRI of this document.'}
          </p>
        </section>

        <section className="card">
          <h3>Reading the data</h3>
          {embedded === true && <PointsNote points={rdf.points} />}
          {csv && (
            <>
              <p className="muted small">How to split the CSV file into rows and columns.</p>
              <table className="facts">
                <tbody>
                  <PropertyRow rdf={rdf} property={TSS_DELIMITER} />
                  <PropertyRow rdf={rdf} property={TSS_HEADER} />
                  <PropertyRow rdf={rdf} property={TSS_ENCODING} />
                </tbody>
              </table>
            </>
          )}
          {parquet && (
            <p className="muted small">No reading settings needed: a Parquet file describes its own columns and their types.</p>
          )}
          {embedded !== true && !csv && !parquet && (
            <p className="muted small">
              {format === null ? 'tss:format is not stated' : <>tss:format is <Term term={format} prefixes={prefixes} /></>}:
              this app reads tss:CSV and tss:Parquet files.
            </p>
          )}
        </section>
      </div>

      <section className="card">
        <h3>Fields ({fields.length})</h3>
        <p className="muted small">
          One field = one {embedded === true ? 'key in each embedded point' : 'column in the data file'}.
          A field that describes another one holds extra information about that field's values, such as a quality code.
        </p>
        <div className="table-scroll">
          <table className="data">
            <thead>
              <tr>
                <th>Column</th>
                {hasIndexes && <th>Index</th>}
                <th>Role</th>
                <th>Label</th>
                <th>Datatype</th>
                <th>Unit</th>
                <th>Observed property</th>
                <th>Sensor</th>
                <th>Feature of interest</th>
                <th>Range</th>
              </tr>
            </thead>
            <tbody>
              {fields.map((field) => (
                <FieldRow key={field.value} rdf={rdf} field={field} hasIndexes={hasIndexes} />
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {others.length > 0 && (
        <section className="card">
          <h3>Other statements ({others.length})</h3>
          <p className="muted small">
            What the document says beyond the cards above, for example a licence, provenance or a sensor, written as in Turtle.
          </p>
          <div className="table-scroll">
            <table className="data">
              <thead>
                <tr>
                  <th>Subject</th>
                  <th>Property</th>
                  <th>Value</th>
                </tr>
              </thead>
              <tbody>
                {others.map((quad, index) => (
                  <tr key={index}>
                    <td className="long-text">{termText(quad.subject, prefixes)}</td>
                    <td className="long-text">{termText(quad.predicate, prefixes)}</td>
                    <td className="long-text">{termText(quad.object, prefixes)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="card">
        <details>
          <summary>Show the metadata document as it was sent ({store.size} triples)</summary>
          <pre>{rdf.text}</pre>
        </details>
      </section>
    </>
  )
}
