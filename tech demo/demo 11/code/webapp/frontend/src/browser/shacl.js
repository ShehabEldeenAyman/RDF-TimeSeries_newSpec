// shacl.js: the SHACL check of a user endpoint, run inside this browser tab (no server).
//
// It gives the same answer as the server's GET/POST /api/endpoints/{id}/shacl: the
// sh:ValidationReport as Turtle. The work is done by pyshacl in Pyodide (Python in
// WebAssembly), in a Web Worker (shaclWorker.js), with the server's own Python code.
//
// Usage:
//   import { validateInBrowser } from './browser/shacl.js'
//
//   // The default shapes, picked by the metadata's tss:embedded (like the server):
//   validateInBrowser({ metadataText: text, contentType: 'text/turtle', baseIRI: documentIri,
//                       shapesText: null, shapesName: null })
//     .then(({ turtle }) => ...)              // the report, as Turtle
//     .catch((err) => setError(err.message))
//
//   // Your own shapes (the text of an uploaded .ttl file):
//   validateInBrowser({ metadataText: text, contentType: 'text/turtle', baseIRI: documentIri,
//                       shapesText: shapesFileText, shapesName: 'my_shapes.ttl' })
//
// The first call loads Pyodide and pyshacl (about 9 MB downloaded from our own server, a few
// seconds; the folder public/vendor/pyodide/ holds 16 MB on disk); later calls reuse the same
// worker and take well under a second.
// The metadata and its embedded points never leave the tab. The only requests are for Pyodide's
// files (/vendor/pyodide/), on our own server, and they are the same whatever the metadata says:
// the two default shapes files are part of the app (shaclWorker.js imports them), so nothing is
// fetched for them.

import { JSON_LD_REFUSED, RDFLIB_FORMATS, UNTYPED_CONTENT_TYPES } from '../rdf.js'

// Where the server serves its bundled shapes (the backend's paths.SHACL_FILES_PATH). The report
// names the default shapes by this address, like the server's report does.
const SHAPES_FOLDER = '/files/SHACL/'

// The backend refuses bigger shapes files, so we do too (Shacl.jsx checks it before reading a file)
export const MAX_SHAPES_BYTES = 5000000

// The worker, started by the first validation and then kept for the whole tab, so
// Pyodide is loaded only once
let worker = null
// The jobs that wait for an answer, by number: { resolve, reject }
const waiting = {}
let lastJobNumber = 0

// The name of rdflib's parser for a Content-Type, e.g. 'text/turtle; charset=utf-8' -> 'turtle'
// (RDFLIB_FORMATS in rdf.js). Throws an Error for JSON-LD and for types that are no RDF syntax we read.
// what: how the error message calls the document
function rdfFormat(contentType, what) {
  let type = contentType ? contentType : ''
  type = type.split(',')[0] // some servers (Zenodo) send the type twice: "a, a"
  type = type.split(';')[0].trim().toLowerCase() // the part after ";" are parameters
  // No type, or a vague one: Turtle, like the backend. (rdf.js has already given your own
  // documents a real type, so this is only a safety net.)
  if (UNTYPED_CONTENT_TYPES.includes(type)) return RDFLIB_FORMATS['text/turtle']
  if (type === 'application/ld+json') throw new Error(`${what} is JSON-LD. ${JSON_LD_REFUSED}`)
  if (!Object.keys(RDFLIB_FORMATS).includes(type)) {
    throw new Error(`${what} is ${type}, which is not an RDF syntax this dashboard reads ` +
      '(Turtle, RDF/XML, N-Triples or N3)')
  }
  return RDFLIB_FORMATS[type]
}

// Start the worker (once), and route its answers to the jobs that wait for them
function getWorker() {
  if (worker !== null) return worker
  // type 'module': the worker uses import, like the rest of the app
  worker = new Worker(new URL('./shaclWorker.js', import.meta.url), { type: 'module' })
  worker.onmessage = (event) => {
    const answer = event.data
    if (answer.loadFailed) {
      // Pyodide could not be loaded, so every waiting job fails. This worker is forgotten:
      // the next check starts a new one (and tries to load Pyodide again).
      stopWorker()
      rejectAll(answer.error)
      return
    }
    const job = waiting[answer.id]
    if (!job) return
    delete waiting[answer.id]
    if (answer.error) {
      job.reject(new Error(answer.error))
    } else {
      job.resolve({ turtle: answer.turtle })
    }
  }
  // The worker script itself failed (could not be loaded, or crashed): every waiting job fails
  worker.onerror = (event) => {
    const message = event.message ? event.message : 'the SHACL worker stopped'
    stopWorker()
    rejectAll(`Could not run the SHACL check in the browser: ${message}`)
  }
  return worker
}

function stopWorker() {
  if (worker !== null) worker.terminate()
  worker = null
}

// Every job that still waits for an answer fails with this message
function rejectAll(message) {
  for (const id of Object.keys(waiting)) {
    waiting[id].reject(new Error(message))
    delete waiting[id]
  }
}

// Validate a metadata document (and its embedded points) with SHACL, in the browser.
//   metadataText: the metadata document's text
//   contentType:  its syntax, e.g. 'text/turtle' (Turtle, N-Triples, N3, RDF/XML; not JSON-LD)
//   baseIRI:      the document's IRI: relative IRIs in it are read against it, like the server does
//   shapesText:   the text of your own shapes (Turtle), or null for the default shapes
//                 (SHACL/embedded_shapes.ttl or external_shapes.ttl, by tss:embedded; bundled with the app)
//   shapesName:   the name of your own shapes file, for error messages (null for the defaults)
// Gives a Promise of { turtle }: the sh:ValidationReport as Turtle. For the default shapes it
// names the file it used, like the server: <report> dcterms:source <http://.../files/SHACL/...>.
export function validateInBrowser({ metadataText, contentType, baseIRI, shapesText, shapesName }) {
  let job
  try {
    if (!baseIRI) throw new Error('A base IRI is needed to read the metadata')
    const custom = shapesText !== null && shapesText !== undefined
    if (custom && new TextEncoder().encode(shapesText).length > MAX_SHAPES_BYTES) {
      throw new Error(`The shapes file must be at most ${MAX_SHAPES_BYTES / 1000000} MB.`)
    }
    job = {
      metadataText: metadataText,
      metadataFormat: rdfFormat(contentType, `The metadata ${baseIRI}`),
      baseIRI: baseIRI,
      // A byte order mark at the start (some editors write one) is skipped, like the server does
      shapesText: custom ? shapesText.replace(/^\uFEFF/, '') : null,
      shapesFormat: 'turtle',
      // Relative IRIs in your own shapes are read against the metadata's IRI
      shapesBase: baseIRI,
      shapesName: custom ? (shapesName ? shapesName : 'the uploaded shapes') : null,
      shapesSource: null, // set by the worker for the default shapes
      // The full address of the default shapes folder on our server (http://...): the report names
      // the default shapes file by its address there. (Only a name: the worker fetches nothing.)
      shapesFolder: new URL(SHAPES_FOLDER, window.location.href).href,
    }
  } catch (error) {
    return Promise.reject(error)
  }

  lastJobNumber += 1
  const id = lastJobNumber
  return new Promise((resolve, reject) => {
    waiting[id] = { resolve: resolve, reject: reject }
    getWorker().postMessage({ id: id, job: job })
  })
}
