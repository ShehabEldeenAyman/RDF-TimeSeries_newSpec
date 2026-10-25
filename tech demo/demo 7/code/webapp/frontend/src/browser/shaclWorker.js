// shaclWorker.js: the SHACL check of a user endpoint, run inside this browser tab.
//
// A Web Worker is a script that runs next to the page, in its own thread, so the page stays
// usable while Python works. shacl.js starts it (only when someone validates for the first
// time) and sends it jobs; this file answers each job with the SHACL report as Turtle.
//
// How it works:
//   1. Pyodide (Python compiled to WebAssembly) is loaded from our own server
//      (/vendor/pyodide/, filled by "npm run fetch-browser-assets"), once per worker.
//   2. Pyodide loads pyshacl and the packages it needs (rdflib, owlrl, ...) from the same
//      folder: the lock file there lists them, with their sha256.
//   3. The server's own Python files are written into Pyodide's (in-memory) file system and
//      imported: code/shacl_report/shacl_report.py and code/TSS2RDF/tss2rdf.py. So the browser
//      runs exactly the validation of the server (validation_report, default_shapes,
//      lift_points) and writes the report with the same exact_turtle.
// Nothing is sent anywhere: the metadata, its embedded points and the report stay in this tab.
// The only requests are for Pyodide's files and the default shapes, all on our own server.
//
// Messages:
//   in:  { id, job }  job = { metadataText, metadataFormat, baseIRI, shapesText, shapesFormat,
//                             shapesBase, shapesName, shapesFolder }  (made by shacl.js)
//   out: { id, turtle }  or  { id, error, loadFailed }

// "?raw" makes Vite give the file's text as a string (at build time), instead of running it.
// These are the server's own files, not copies: a change there reaches the browser too.
import shaclReportSource from '../../../../shacl_report/shacl_report.py?raw'
import tss2rdfSource from '../../../../TSS2RDF/tss2rdf.py?raw'

// Where Pyodide's files are: the public/vendor/pyodide/ folder, served at /vendor/pyodide/.
// Pyodide needs the full address (with http://...), not just a path.
const PYODIDE_URL = new URL(import.meta.env.BASE_URL + 'vendor/pyodide/', self.location.origin).href

// Where the server's Python files go inside Pyodide, in the same folders as in the repository
// (shacl_report.py imports "TSS2RDF.tss2rdf" from the folder above its own)
const CODE_DIR = '/home/pyodide/code'

// The Python side. JavaScript and Python talk in JSON text: a job goes in as JSON, and the
// answer comes back as JSON, { "turtle": ... } or { "error": ... }.
const PYTHON_GLUE = `
import json
import logging
import sys
import warnings

sys.path.insert(0, "${CODE_DIR}")

from rdflib import Graph, RDF, URIRef
from rdflib.namespace import DCTERMS, SH

# The server's own code (the files written above)
from shacl_report import shacl_report
from TSS2RDF.tss2rdf import exact_turtle

# Like the server: no long traceback for every value that does not fit its datatype
# (like "abc"^^xsd:double), and no warnings for a boolean like "yes". The report lists them.
logging.getLogger("rdflib.term").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", message="Parsing weird boolean")
warnings.filterwarnings("ignore", message="Serializing weird numerical")


def read_metadata(job):
    """The metadata as an rdflib graph, read with its base IRI (like documents.parse_document)"""
    graph = Graph()
    try:
        graph.parse(data=job["metadataText"], format=job["metadataFormat"], publicID=job["baseIRI"])
    except Exception as error:  # for example a typo in the Turtle
        raise ValueError(f"Could not read {job['baseIRI']}: {error}")
    return graph


def has_shapes(shapes):
    """Whether a graph holds SHACL shapes: the same check as shacl_routes.has_shapes on the server"""
    for kind in (SH.NodeShape, SH.PropertyShape):
        if (None, RDF.type, kind) in shapes:
            return True
    for prop in (SH.targetClass, SH.targetNode, SH.targetSubjectsOf, SH.targetObjectsOf, SH.property, SH.path):
        if (None, prop, None) in shapes:
            return True
    return False


def pick_default_shapes(job_json):
    """The file name of the bundled shapes for this metadata (embedded_shapes.ttl or
    external_shapes.ttl), picked by tss:embedded like the server does (shacl_report.default_shapes)"""
    job = json.loads(job_json)
    try:
        graph = read_metadata(job)
    except ValueError as error:
        return json.dumps({"error": str(error)})
    path = shacl_report.default_shapes(graph)
    if path is None:
        return json.dumps({"error": "Cannot pick the default shapes: the metadata needs a tss:Source with "
                                    "tss:embedded true or false. You can upload your own shapes instead."})
    return json.dumps({"name": path.name})


def make_report(job_json):
    """Validate the metadata (and its embedded points) with the shapes, like the server's
    GET and POST /shacl: shacl_report.validation_report, written with exact_turtle"""
    job = json.loads(job_json)
    try:
        graph = read_metadata(job)
    except ValueError as error:
        return json.dumps({"error": str(error)})

    shapes = Graph()
    try:
        shapes.parse(data=job["shapesText"], format=job["shapesFormat"], publicID=job["shapesBase"])
    except Exception as error:
        return json.dumps({"error": f"Could not read the shapes {job['shapesName']}: {error}"})
    if not has_shapes(shapes):
        return json.dumps({"error": f"The shapes file {job['shapesName']} contains no SHACL shapes or targets."})

    try:
        report, report_node, point_triples = shacl_report.validation_report(graph, shapes)
    except ValueError as error:
        return json.dumps({"error": f"Could not validate with {job['shapesName']}: {error}"})

    # Which shapes were used, like the server says for its default shapes: their address
    if job.get("shapesSource"):
        report.add((report_node, DCTERMS.source, URIRef(job["shapesSource"])))
    return json.dumps({"turtle": exact_turtle(report)})
`

// The Python functions, once Pyodide is ready: a Promise, made by the first job and then
// shared by every job, so Pyodide is loaded only once for this worker (it lives as long as the tab)
let pythonReady = null

function loadPython() {
  if (pythonReady === null) {
    pythonReady = startPython()
  }
  return pythonReady
}

function startPython() {
  // A dynamic import of a full address: Vite leaves it alone (@vite-ignore), so the browser
  // loads pyodide.mjs from /vendor/pyodide/ when the first job arrives
  return import(/* @vite-ignore */ PYODIDE_URL + 'pyodide.mjs')
    .catch((error) => {
      throw new Error(`Could not load Pyodide from ${PYODIDE_URL} (${error.message}). ` +
        'Run "npm run fetch-browser-assets" in code/webapp/frontend, then try again.')
    })
    .then((pyodideModule) => {
      // indexURL: where the interpreter and the standard library are.
      // lockFileURL: the list of packages Pyodide may load (written by the fetch script);
      // their wheel files are in the same folder, so nothing comes from a CDN.
      return pyodideModule.loadPyodide({
        indexURL: PYODIDE_URL,
        lockFileURL: PYODIDE_URL + 'pyodide-lock.json',
      })
    })
    .then((pyodide) => {
      // pyshacl and what it needs (rdflib, owlrl, prettytable, packaging, pyparsing, wcwidth)
      return pyodide.loadPackage(['pyshacl']).then(() => pyodide)
    })
    .then((pyodide) => {
      // The server's Python files, in the same folders as in the repository
      pyodide.FS.mkdirTree(CODE_DIR + '/shacl_report')
      pyodide.FS.mkdirTree(CODE_DIR + '/TSS2RDF')
      pyodide.FS.writeFile(CODE_DIR + '/shacl_report/shacl_report.py', shaclReportSource)
      pyodide.FS.writeFile(CODE_DIR + '/TSS2RDF/tss2rdf.py', tss2rdfSource)
      pyodide.runPython(PYTHON_GLUE)
      return {
        pickDefaultShapes: pyodide.globals.get('pick_default_shapes'),
        makeReport: pyodide.globals.get('make_report'),
      }
    })
    .catch((error) => {
      // Mark the error, so shacl.js knows this worker is broken and starts a new one next time
      const failed = new Error(shortError(error))
      failed.loadFailed = true
      throw failed
    })
}

// A Python error's message is its whole traceback; its last line says what went wrong,
// e.g. "ModuleNotFoundError: No module named 'pyshacl'"
function shortError(error) {
  const lines = String(error.message).trim().split('\n')
  return lines[lines.length - 1]
}

// The default shapes file, from the server's /files/SHACL/ folder (shapes are not data)
function fetchShapes(url) {
  // no-cache: ask the server each time (cheap when unchanged), so an edited shapes file
  // is used at once, like the server reads its file for every check
  return fetch(url, { cache: 'no-cache' })
    .catch((error) => {
      throw new Error(`Could not reach ${url} (${error.message}). Is the backend running?`)
    })
    .then((response) => {
      if (!response.ok) {
        throw new Error(`Could not load the default shapes ${url}: ${response.status} ${response.statusText}`)
      }
      // The Vite dev server answers a path it does not know with the app's index.html
      const contentType = response.headers.get('Content-Type') || ''
      if (contentType.startsWith('text/html')) {
        throw new Error(`Could not load the default shapes ${url}: the server sent an HTML page, not Turtle. ` +
          'Is the backend running?')
      }
      return response.text()
    })
}

// Python answers in JSON: the report's Turtle, or an error message
function readAnswer(answerJson) {
  const answer = JSON.parse(answerJson)
  if (answer.error) throw new Error(answer.error)
  return answer
}

// One job: pick and fetch the default shapes when none were given, then validate.
// Gives a Promise of the report as Turtle.
function validate(job) {
  return loadPython()
    .then((python) => {
      if (job.shapesText !== null) {
        return { python: python, job: job } // custom shapes: nothing to fetch
      }
      // The bundled shapes for this metadata's tss:embedded, picked by the server's code
      const picked = readAnswer(python.pickDefaultShapes(JSON.stringify(job)))
      const url = new URL(picked.name, job.shapesFolder).href
      return fetchShapes(url).then((text) => {
        const withShapes = {
          ...job,
          shapesText: text,
          shapesFormat: 'turtle',
          shapesBase: url,
          shapesName: picked.name, // how error messages call the shapes
          shapesSource: url,       // the report names the shapes file (dcterms:source)
        }
        return { python: python, job: withShapes }
      })
    })
    .then(({ python, job }) => {
      const answer = readAnswer(python.makeReport(JSON.stringify(job)))
      return answer.turtle
    })
}

self.onmessage = (event) => {
  const { id, job } = event.data
  validate(job)
    .then((turtle) => {
      self.postMessage({ id: id, turtle: turtle })
    })
    .catch((error) => {
      self.postMessage({ id: id, error: error.message, loadFailed: error.loadFailed === true })
    })
}
