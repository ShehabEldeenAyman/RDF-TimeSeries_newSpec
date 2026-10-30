// shaclReport.js: reading a SHACL validation report (an N3 Store, parsed from the backend's Turtle)
// into what the page shows. Two tabs show a report: the SHACL tab (Shacl.jsx) and the "Add data"
// tab (AddPointForm.jsx, the check of a new point). Both draw it with ShaclReport (in Shacl.jsx).
//
// Usage:
//   parseRdf(reportTurtle, 'text/turtle', baseIri).then(({ store, prefixes }) => {
//     const read = readReport(store)   // { conforms, shapesSource, results, counts }; throws when
//                                      // the store holds no sh:ValidationReport, or more than one
//     const shown = mergePrefixes(rdf.prefixes, prefixes)
//   })

import { DataFactory } from 'n3'
import {
  one, all, oneText, booleanValue,
  RDF_TYPE, SH_VALIDATION_REPORT, SH_CONFORMS, SH_RESULT, SH_FOCUS_NODE, SH_RESULT_PATH, SH_VALUE,
  SH_RESULT_SEVERITY, SH_RESULT_MESSAGE, SH_SOURCE_SHAPE, SH_VIOLATION, SH_WARNING, SH_INFO, DCTERMS_SOURCE,
} from './tss'

const { namedNode } = DataFactory

// The severities of SHACL, worst first (to sort the table)
const SEVERITIES = [SH_VIOLATION, SH_WARNING, SH_INFO]

// Read the parts of a SHACL validation report (an N3 Store) that the page shows:
//   conforms      true, false, or null when the report does not say
//   shapesSource  the IRI of the shapes file (dcterms:source), or null
//   results       one object per sh:ValidationResult, worst severity first
//   counts        { violations, warnings, infos }
export function readReport(store) {
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
export function mergePrefixes(metadataPrefixes, reportPrefixes) {
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
