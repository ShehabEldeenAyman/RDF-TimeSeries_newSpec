import { fieldName } from './fields'
import { readSummary, shortName, termText, SUMMARY_ATTRIBUTES } from './tss'

// The stored summaries of fields (tss:summary in the metadata): numbers computed from the
// data when the metadata was made (prov:generatedAtTime), not refreshed since. The app shows
// them as the metadata states them; the Query tab and the SQL box compare them with the data.

// The attributes that differ from field to field: the columns of the summaries table
const TABLE_KEYS = ['count', 'missing', 'firstTime', 'lastTime', 'minimum', 'maximum', 'mean', 'median']
// The attributes that are usually the same for every field: shown once, above the table
// (when one of them differs between fields, it gets a column too)
const SHARED_KEYS = ['from', 'until', 'policy', 'generatedAt']

// The name people read for an attribute, e.g. 'firstTime' -> 'first time'
function attributeName(key) {
  for (const attribute of SUMMARY_ATTRIBUTES) {
    if (attribute.key === key) return attribute.name
  }
  return key
}

// Are two terms (or nulls) the same? equals compares two RDF terms: the same IRI, or the
// same literal (text, datatype and language)
function sameTerm(a, b) {
  if (a === null || b === null) return a === b
  return a.equals(b)
}

// One value of a summary: an IRI as its short name, a literal as its text (hover over it:
// the literal as in Turtle, with its datatype), or a dash when the summary does not state it
export function SummaryValue({ term, prefixes }) {
  if (term === null) return '—'
  if (term.termType === 'NamedNode') return <span title={term.value}>{shortName(term, prefixes)}</span>
  return <span title={termText(term, prefixes)}>{term.value}</span>
}

// The summaries of several fields, as one table with a row per field that has a summary
//   rdf: the parsed document ({ store, prefixes, ... })   fields: the fields (NamedNodes)
export function SummaryTable({ rdf, fields }) {
  const rows = [] // { field, summary } for each field with a summary
  for (const field of fields) {
    const summary = readSummary(rdf.store, field)
    if (summary !== null) rows.push({ field: field, summary: summary })
  }
  if (rows.length === 0) return <p className="muted small">No field has a stored summary (tss:summary).</p>

  // An attribute that every summary states with the same text is shown once, above the table;
  // one that differs between fields gets its own column
  const shared = []
  const columns = TABLE_KEYS.slice() // slice(): a copy, so adding to it does not change TABLE_KEYS
  for (const key of SHARED_KEYS) {
    const first = rows[0].summary.values[key]
    let sameEverywhere = true
    for (const row of rows) {
      if (!sameTerm(row.summary.values[key], first)) sameEverywhere = false
    }
    if (!sameEverywhere) columns.push(key)
    else if (first !== null) shared.push(key) // (stated by no summary at all: not shown)
  }

  return (
    <>
      {shared.length > 0 && (
        <table className="facts">
          <tbody>
            {shared.map((key) => (
              <tr key={key}>
                <th>{attributeName(key)}</th>
                <td><SummaryValue term={rows[0].summary.values[key]} prefixes={rdf.prefixes} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="table-scroll">
        <table className="data">
          <thead>
            <tr>
              <th>Column</th>
              {columns.map((key) => <th key={key}>{attributeName(key)}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.field.value}>
                <td title={row.field.value}><code>{fieldName(rdf.store, row.field, rdf.prefixes)}</code></td>
                {columns.map((key) => (
                  <td key={key}><SummaryValue term={row.summary.values[key]} prefixes={rdf.prefixes} /></td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

// The summary of one field, as a facts table with every attribute (a dash when it is not stated)
export function SummaryFacts({ rdf, field }) {
  const summary = readSummary(rdf.store, field)
  if (summary === null) return <p className="muted small">This field has no stored summary (tss:summary).</p>
  return (
    <table className="facts">
      <tbody>
        {SUMMARY_ATTRIBUTES.map((attribute) => (
          <tr key={attribute.key}>
            <th title={attribute.iri}>{attribute.name}</th>
            <td><SummaryValue term={summary.values[attribute.key]} prefixes={rdf.prefixes} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
