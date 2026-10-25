import { useRef, useState } from 'react'
import { DataFactory } from 'n3'
import { parseRdf } from './rdf'
import {
  one, all, oneText, isEmbedded, booleanValue, shortName, termText,
  RDF_TYPE, RDFS_LABEL, TSS_FORMAT,
  SH_VALIDATION_REPORT, SH_CONFORMS, SH_RESULT, SH_FOCUS_NODE, SH_RESULT_PATH, SH_VALUE,
  SH_RESULT_SEVERITY, SH_RESULT_MESSAGE, SH_SOURCE_SHAPE, SH_VIOLATION, SH_WARNING, SH_INFO,
  SH_NAME, DCTERMS_SOURCE,
} from './tss'

const { namedNode } = DataFactory

// The backend refuses bigger shapes files, so we check this before sending one
const MAX_SHAPES_BYTES = 5000000

// The severities of SHACL, worst first (to sort the table)
const SEVERITIES = [SH_VIOLATION, SH_WARNING, SH_INFO]

// The SHACL tab: pyshacl validates the metadata (and, for embedded points, the points read as
// RDF) and answers with the validation report as Turtle. For a bundled endpoint pyshacl runs in
// the backend; for your own endpoint it runs in this browser tab (Python in WebAssembly, Pyodide).
// The api (dataApi.js) asks the right one. We parse the report here and show what it says.
export function Shacl({ rdf, api }) {
  const [report, setReport] = useState(null)             // the report, read by readReport
  const [running, setRunning] = useState(false)
  const [loadingPython, setLoadingPython] = useState(false) // the first check in this tab loads Python first
  const [error, setError] = useState(null)
  const [customShapes, setCustomShapes] = useState(null) // { name, text, triples } of an uploaded file
  const [reading, setReading] = useState(false)
  const fileInput = useRef(null)

  // Each run gets a number. An answer for an older run (when you clicked again before
  // it arrived) is thrown away, like the "ignore" trick in App.jsx.
  const runNumber = useRef(0)

  // The default shapes are picked by tss:embedded (embedded points, or an external data
  // file), so that only works when the source states tss:embedded true or false
  const embedded = isEmbedded(rdf.store, rdf.source) // true, false, or null when not stated
  const known = embedded !== null
  const format = one(rdf.store, rdf.source, TSS_FORMAT)

  // The base IRI for uploaded shapes: the relative IRIs in them are read against it (the same
  // IRI the validation uses, see shaclBase in dataApi.js)
  const shapesBase = api.shaclBase

  function selectFile(event) {
    const file = event.target.files[0]
    setReport(null)
    setError(null)
    setCustomShapes(null)
    if (!file) return
    if (file.size > MAX_SHAPES_BYTES) {
      setError('The shapes file must be at most 5 MB.')
      event.target.value = ''
      return
    }

    // Read the file, and parse it here first: a typing error in the Turtle is then
    // reported at once, with its line number, before anything is sent
    setReading(true)
    file.text()
      .then((text) => {
        if (text.trim() === '') throw new Error('the file is empty')
        return parseRdf(text, 'text/turtle', shapesBase).then((parsed) => {
          setCustomShapes({ name: file.name, text: text, triples: parsed.store.size })
        })
      })
      .catch((err) => {
        setError(`Could not use ${file.name} as Turtle shapes: ${err.message}`)
        if (fileInput.current) fileInput.current.value = ''
      })
      .finally(() => setReading(false))
  }

  function useDefaults() {
    setCustomShapes(null)
    setReport(null)
    setError(null)
    if (fileInput.current) fileInput.current.value = ''
  }

  function runValidation() {
    runNumber.current += 1
    const thisRun = runNumber.current
    const started = performance.now() // to measure, here in the browser, how long it takes
    const shapesName = customShapes ? customShapes.name : null
    setRunning(true)
    setLoadingPython(api.shaclStarting())
    setReport(null)
    setError(null)

    // Your own shapes go along (their text); null means: the default shapes
    api.shacl(customShapes ? customShapes.text : null, shapesName)
      .then((loaded) => {
        if (thisRun !== runNumber.current) return // a newer run started: this answer is old
        const read = readReport(loaded.store)
        setReport({
          store: loaded.store, // the report as RDF, to look up blank nodes in it
          text: loaded.text,   // the report as Turtle, as pyshacl wrote it
          // To show IRIs short: the metadata's prefixes (ex:, tss:, ...) plus the report's own (sh:, ...)
          prefixes: mergePrefixes(rdf.prefixes, loaded.prefixes),
          conforms: read.conforms,
          shapesSource: read.shapesSource,
          results: read.results,
          counts: read.counts,
          shapesName: shapesName,
          seconds: ((performance.now() - started) / 1000).toFixed(2),
        })
      })
      .catch((err) => {
        if (thisRun === runNumber.current) setError(err.message)
      })
      .finally(() => {
        if (thisRun === runNumber.current) setRunning(false)
      })
  }

  return (
    <section className="card">
      <h3>SHACL conformance</h3>
      {embedded === false && (
        <p>
          Detected <strong>external data</strong>: <code>tss:embedded false</code>,{' '}
          <code>tss:format {format ? shortName(format, rdf.prefixes) : '(not stated)'}</code>.
        </p>
      )}
      {embedded === true && (
        <p>
          Detected <strong>embedded points</strong>: <code>tss:embedded true</code>. The points are read as RDF too
          (JSON-LD with a context made from the fields), so the shapes can check every value.
        </p>
      )}
      {!known && (
        <p className="muted">
          Cannot pick the default shapes: the source must state tss:embedded true or false. You can upload your own
          shapes instead.
        </p>
      )}
      <p>
        <label htmlFor="shacl-file">Upload custom SHACL shapes (.ttl)</label>{' '}
        <input
          ref={fileInput}
          id="shacl-file"
          type="file"
          accept=".ttl,text/turtle"
          disabled={running || reading}
          onChange={selectFile}
        />
      </p>
      {reading && <p role="status">Reading the shapes file…</p>}
      {customShapes && (
        <p>
          Using custom shapes: <strong>{customShapes.name}</strong> ({customShapes.triples} triples, read without
          errors). These replace the default shapes for this test.{' '}
          <button className="secondary" disabled={running || reading} onClick={useDefaults}>
            Use default shapes
          </button>
        </p>
      )}
      {/* The default shapes file is picked by tss:embedded; after a run, the report names it (dcterms:source) */}
      {!customShapes && known && (
        <p>
          Using the default shapes for this source: {api.inBrowser ? 'they are picked' : 'the backend picks them'} by{' '}
          <code>tss:embedded</code>, and the report names the file it used.
        </p>
      )}
      {api.inBrowser && (
        <p className="muted small">
          The check runs here, in this browser tab: pyshacl in Python, which the browser loads the first time
          (a few seconds). The metadata and its points are not sent anywhere; only the bundled shapes and
          Python's files come from the demo's server.
        </p>
      )}
      <p className="muted small">
        Checks the loaded Turtle against the selected shapes. Warnings are shown but do not make the test fail.
        Custom files are used for this session and do not replace the bundled files.
      </p>
      <button disabled={running || reading || (!known && !customShapes)} onClick={runValidation}>
        {running ? 'Checking…' : 'Run conformance test'}
      </button>
      {/* The first check of your own endpoint in this tab loads Python first: say why it takes a while */}
      {running && loadingPython && <p role="status">Loading Python for the SHACL check (first time in this tab)…</p>}
      {error && <p className="error long-text" role="alert">{error}</p>}
      {report && <ShaclReport report={report} rdf={rdf} inBrowser={api.inBrowser} />}
    </section>
  )
}

// What the report says: conforms or not, the counts, and one table row per result
//   inBrowser: true when the check ran in this browser tab
function ShaclReport({ report, rdf, inBrowser }) {
  let conformsText = 'sh:conforms not stated'
  if (report.conforms === true) conformsText = 'Conforms'
  if (report.conforms === false) conformsText = 'Does not conform'

  // Which shapes: an upload, or the bundled file the report names (dcterms:source)
  let shapesText
  if (report.shapesName) {
    shapesText = <>{report.shapesName} (custom upload)</>
  } else if (report.shapesSource) {
    shapesText = (
      <>
        <a href={report.shapesSource.value} target="_blank" rel="noreferrer">{report.shapesSource.value}</a> (default,
        named by the report's dcterms:source)
      </>
    )
  } else {
    shapesText = 'the default shapes (the report does not name the file)'
  }

  return (
    <div className="result" aria-live="polite">
      <p>
        <span className={report.conforms === true ? 'badge ok' : 'badge bad'}>{conformsText}</span>{' '}
        {report.counts.violations} violations · {report.counts.warnings} warnings ·{' '}
        {report.counts.infos} infos · {report.seconds} s
      </p>
      <p className="muted small long-text">Shapes: {shapesText}</p>
      <p className="muted small">
        {inBrowser
          ? 'The time is measured in the browser: the validation in this tab (the first time also loading Python) and reading the report.'
          : 'The time is measured in the browser: the request, the validation and reading the report.'}
      </p>

      {report.results.length > 0 && (
        <div className="table-scroll">
          <table className="data">
            <thead>
              <tr>
                <th>Severity</th>
                <th>Focus node</th>
                <th>Path</th>
                <th>Value</th>
                <th>Message</th>
                <th>Source shape</th>
              </tr>
            </thead>
            <tbody>
              {report.results.map((result) => (
                <ResultRow key={result.node.value} result={result} report={report} rdf={rdf} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      <details>
        <summary>Show the report as Turtle</summary>
        <pre className="shacl-report" tabIndex={0} aria-label="SHACL validation report">{report.text}</pre>
      </details>
    </div>
  )
}

// One sh:ValidationResult
function ResultRow({ result, report, rdf }) {
  const prefixes = report.prefixes
  // The focus node is usually the source or a field: its label is in the metadata.
  // Only an IRI means the same thing in both documents; a blank node of the report
  // (e.g. _:b0) has nothing to do with a blank node of the metadata that happens to
  // have the same name, so we don't look those up.
  const focusIsIri = result.focusNode !== null && result.focusNode.termType === 'NamedNode'
  const focusLabel = focusIsIri ? oneText(rdf.store, result.focusNode, RDFS_LABEL) : ''
  const violation = result.severity !== null && result.severity.value === SH_VIOLATION

  return (
    <tr>
      <td className={violation ? 'error' : ''}>{result.severity ? shortName(result.severity, prefixes) : '—'}</td>
      <td className="long-text">
        {nodeText(report.store, result.focusNode, prefixes)}
        {focusLabel !== '' && <div className="muted small">{focusLabel}</div>}
      </td>
      <td className="long-text">{nodeText(report.store, result.path, prefixes)}</td>
      <td className="long-text">{nodeText(report.store, result.value, prefixes)}</td>
      <td className="long-text">{result.message !== '' ? result.message : '—'}</td>
      <td className="long-text">{shapeText(report.store, result.sourceShape, prefixes)}</td>
    </tr>
  )
}

// --- Reading the report -----------------------------------------------------------

// Read the parts of a SHACL validation report (an N3 Store) that the page shows:
//   conforms      true, false, or null when the report does not say
//   shapesSource  the IRI of the shapes file (dcterms:source), or null
//   results       one object per sh:ValidationResult, worst severity first
//   counts        { violations, warnings, infos }
function readReport(store) {
  const reports = store.getSubjects(namedNode(RDF_TYPE), namedNode(SH_VALIDATION_REPORT), null)
  if (reports.length !== 1) {
    throw new Error(`The answer holds ${reports.length} sh:ValidationReport nodes, not one.`)
  }
  const reportNode = reports[0]

  const results = []
  const counts = { violations: 0, warnings: 0, infos: 0 }
  for (const node of all(store, reportNode, SH_RESULT)) {
    const result = {
      node: node,
      severity: one(store, node, SH_RESULT_SEVERITY),
      focusNode: one(store, node, SH_FOCUS_NODE),
      path: one(store, node, SH_RESULT_PATH),
      value: one(store, node, SH_VALUE),
      message: oneText(store, node, SH_RESULT_MESSAGE),
      sourceShape: one(store, node, SH_SOURCE_SHAPE),
    }
    results.push(result)

    // Count the severities by their full IRI
    const severity = result.severity ? result.severity.value : ''
    if (severity === SH_VIOLATION) counts.violations += 1
    if (severity === SH_WARNING) counts.warnings += 1
    if (severity === SH_INFO) counts.infos += 1
  }
  // RDF gives the results in no particular order: violations first, then by message
  results.sort((a, b) => {
    const bySeverity = severityRank(a.severity) - severityRank(b.severity)
    if (bySeverity !== 0) return bySeverity
    return a.message.localeCompare(b.message, 'en')
  })

  return {
    conforms: booleanValue(one(store, reportNode, SH_CONFORMS)),
    shapesSource: one(store, reportNode, DCTERMS_SOURCE),
    results: results,
    counts: counts,
  }
}

// The prefixes to show the report with: all of the metadata's own, plus those of the
// report for namespaces the metadata has no prefix for. (The backend's rdflib may call
// https://example.org/ex# "ex1:" in the report; we want "ex:", the name the metadata uses.)
function mergePrefixes(metadataPrefixes, reportPrefixes) {
  const merged = Object.assign({}, metadataPrefixes)
  const known = Object.values(metadataPrefixes) // the namespaces that already have a prefix
  for (const prefix of Object.keys(reportPrefixes)) {
    const namespace = reportPrefixes[prefix]
    if (!known.includes(namespace) && !(prefix in merged)) merged[prefix] = namespace
  }
  return merged
}

// 0 for sh:Violation, 1 for sh:Warning, 2 for sh:Info, 3 for anything else
function severityRank(severity) {
  const rank = severity ? SEVERITIES.indexOf(severity.value) : -1
  return rank === -1 ? SEVERITIES.length : rank
}

// A term of the report as text, for display only. A blank node has no name, so we
// show what the report says about it instead, e.g. the point of a P1 result:
//   [ ex:zevenaarshaven-ec20-value-field "4797.72"^^xsd:double ]
function nodeText(store, term, prefixes) {
  if (term === null) return '—'
  if (term.termType !== 'BlankNode') return termText(term, prefixes)
  const parts = []
  for (const quad of store.getQuads(term, null, null, null)) {
    parts.push(termText(quad.predicate, prefixes) + ' ' + termText(quad.object, prefixes))
  }
  if (parts.length === 0) return termText(term, prefixes)
  return '[ ' + parts.join(' ; ') + ' ]'
}

// The shape a result comes from: its sh:name when it has one (e.g. "S6 points"),
// else its IRI, else (a blank node) what the report says about it
function shapeText(store, shape, prefixes) {
  if (shape === null) return '—'
  const name = oneText(store, shape, SH_NAME)
  if (name !== '') return name
  return nodeText(store, shape, prefixes)
}
