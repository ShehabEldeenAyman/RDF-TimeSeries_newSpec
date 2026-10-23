// rdf.js: the one place where the app turns RDF text into RDF data, and back.
//
// RDF in two sentences: every fact is a triple "subject predicate object", e.g.
//   <https://example.org/ex#grootdok-source>  tss:format  tss:CSV .
// A quad is a triple plus a fourth part, the graph it belongs to (here always the
// default graph), so the RDF libraries in JavaScript talk about quads.
//
// We use two libraries:
//   - rdf-parse reads RDF in any common syntax (Turtle, JSON-LD, RDF/XML, ...) and gives quads.
//   - n3 (N3.js) gives a Store: an in-memory database of quads that we can ask questions,
//     e.g. "which objects does this subject have for tss:hasField?". See tss.js for that.
//
// Usage:
//   import { loadRdf, parseRdf, writeTurtle } from './rdf.js'
//
//   // Fetch a document and parse it. The base IRI is the URL we fetched from,
//   // unless we pass the document's real IRI (needed for proxied online documents).
//   loadRdf('/files/metadata%20endpoint/waterinfo_289423042_tss2.ttl')
//     .then((rdf) => {
//       console.log(rdf.store.size, 'quads')   // rdf.store is an N3 Store
//       console.log(rdf.prefixes.tss)          // 'https://w3id.org/tss#'
//       console.log(rdf.text)                  // the Turtle text as it was sent
//     })
//     .catch((err) => setError(err.message))
//
//   // Parse text we already have (an uploaded file, a test):
//   parseRdf(turtleText, 'text/turtle', 'http://localhost:5174/upload.ttl')
//     .then(({ store, prefixes }) => ...)
//
//   // Show quads as Turtle:
//   writeTurtle(store.getQuads(null, null, null, null), prefixes).then((turtle) => ...)
//
//   // A POST or DELETE whose answer is Turtle (or an error as JSON): read it like loadRdf does
//   fetch(url, { method: 'DELETE' })
//     .then((response) => readRdfAnswer(response, response.url))
//     .then(({ store, prefixes, text }) => ...)

import { rdfParser } from 'rdf-parse'
import { Store, Writer } from 'n3'
import { Readable } from 'readable-stream'
import { errorText } from './config.js'

// The syntax we assume when a server does not say what it sent. Many simple servers
// (for example raw files on GitHub) send Turtle as text/plain or as plain bytes.
const DEFAULT_CONTENT_TYPE = 'text/turtle'
const UNTYPED_CONTENT_TYPES = ['', 'text/plain', 'application/octet-stream']

// Fetch an RDF document and parse it into a new N3 Store.
//   url:     where to fetch it, e.g. '/api/catalog' (a path on our own server is fine)
//   baseIRI: the IRI of the document itself. Relative IRIs in the document, like
//            <../data/waterinfo_289423042.csv>, are resolved against it. Leave it out
//            to use the URL we fetch from.
// Gives a Promise of { store, prefixes, text, url, baseIRI }:
//   store    the quads, in an N3 Store
//   prefixes the @prefix lines of the document, e.g. { tss: 'https://w3id.org/tss#' }
//   text     the document text exactly as the server sent it (to show it)
//   url      the absolute URL we fetched from
//   baseIRI  the base IRI the relative IRIs were resolved against
export function loadRdf(url, baseIRI) {
  // fetch accepts a path like '/api/catalog', but a base IRI must be a full IRI.
  // new URL(path, page address) makes 'http://localhost:5174/api/catalog' from it.
  const absoluteUrl = new URL(url, window.location.href).href
  const base = baseIRI ? baseIRI : absoluteUrl
  let contentType = DEFAULT_CONTENT_TYPE

  return fetch(absoluteUrl)
    // fetch itself fails only when there is no answer at all (server down, no network).
    // The browser then only says "Failed to fetch", so we add where we tried to go.
    .catch((err) => {
      throw new Error(`Could not reach ${absoluteUrl} (${err.message}). Is the backend running? Check its terminal.`)
    })
    .then((response) => {
      // A 404 or 500 still "succeeds" for fetch, so we check the status ourselves.
      if (!response.ok) return throwHttpError(response, absoluteUrl)
      contentType = mediaType(response.headers.get('Content-Type'))
      // For a path it does not know, the Vite dev server answers with the app's own
      // index.html and status 200. rdf-parse would read that HTML without an error and
      // find 0 quads, so we stop here with a message that says what went wrong.
      if (contentType === 'text/html') {
        throw new Error(`Could not load ${absoluteUrl}: the server sent an HTML page, not RDF. Is the path right, and is the backend running?`)
      }
      return response.text()
    })
    .then((text) => {
      return parseRdf(text, contentType, base).then((parsed) => {
        return { store: parsed.store, prefixes: parsed.prefixes, text: text, url: absoluteUrl, baseIRI: base }
      })
    })
}

// Parse RDF text into a new N3 Store, without fetching anything.
//   text:        the RDF document
//   contentType: its syntax, e.g. 'text/turtle' or 'application/ld+json'
//   baseIRI:     the IRI of the document (always give one, see below)
//   moreOptions: optional extra options for rdf-parse (tss.js uses it to give the
//                JSON-LD parser a context)
// Gives a Promise of { store, prefixes }.
export function parseRdf(text, contentType, baseIRI, moreOptions) {
  // Without a base IRI, rdf-parse does not complain: <../data/x.csv> silently becomes
  // </data/x.csv>, which is wrong. So we refuse to parse without one.
  if (!baseIRI) {
    return Promise.reject(new Error('parseRdf needs a base IRI (the IRI of the document)'))
  }

  const options = { contentType: contentType, baseIRI: baseIRI }
  if (moreOptions) Object.assign(options, moreOptions)

  return new Promise((resolve, reject) => {
    const store = new Store()
    const prefixes = {}

    // rdf-parse reads from a Node-style stream (it was written for Node.js first).
    // Readable.from([text]) makes such a stream that gives our text in one piece.
    // (A fetch response body is a different, browser-style stream, so we read the
    // whole text first and wrap it.)
    const textStream = Readable.from([text])

    // parse() answers at once with a stream of quads; the quads arrive as "events":
    //   'data'   one quad was read: we put it in the store
    //   'prefix' an @prefix line was read: we remember it (iri is a NamedNode, so .value)
    //   'error'  the text is not valid RDF in this syntax
    //   'end'    everything was read
    const quadStream = rdfParser.parse(textStream, options)
    quadStream.on('data', (quad) => store.addQuad(quad))
    quadStream.on('prefix', (prefix, iri) => {
      prefixes[prefix] = iri.value
    })
    quadStream.on('error', (err) => reject(new Error(`Could not parse the ${contentType} document ${baseIRI}: ${err.message}`)))
    quadStream.on('end', () => resolve({ store: store, prefixes: prefixes }))
  })
}

// A backend answer that is RDF (Turtle) when it went well, and an error as JSON when not:
// { "detail": "..." }. Gives a Promise of { store, prefixes, text }, like loadRdf, or a rejected
// Promise with the backend's message. Reading the error as JSON is fine: it is not RDF.
//   response: what fetch gave;  baseIRI: the IRI of the answer (e.g. response.url, the full
//             address the request went to)
// (Used for the answers of adding and removing an online endpoint, and of a SHACL test with
// uploaded shapes.)
export function readRdfAnswer(response, baseIRI) {
  if (!response.ok) {
    const statusText = `${response.status} ${response.statusText}`
    return response
      .json()
      .catch(() => ({})) // the error was not JSON: use the status text
      .then((body) => {
        throw new Error(body.detail ? errorText(body.detail) : statusText)
      })
  }
  return response.text().then((text) => {
    return parseRdf(text, 'text/turtle', baseIRI).then((parsed) => {
      return { store: parsed.store, prefixes: parsed.prefixes, text: text }
    })
  })
}

// Write quads as Turtle text, to show them. Consecutive quads with the same subject
// are grouped with ";", so pass them grouped (store.getQuads(...) already does that).
//   quads:    an array of quads, e.g. store.getQuads(null, null, null, null)
//   prefixes: e.g. { tss: 'https://w3id.org/tss#' }, used to shorten IRIs to tss:CSV
// Gives a Promise of the Turtle text.
export function writeTurtle(quads, prefixes) {
  return new Promise((resolve, reject) => {
    const writer = new Writer({ prefixes: prefixes ? prefixes : {}, format: 'text/turtle' })
    writer.addQuads(quads)
    writer.end((err, turtle) => {
      if (err) reject(err)
      else resolve(turtle)
    })
  })
}

// 'text/turtle; charset=utf-8' -> 'text/turtle'. The part after ";" are parameters,
// which the parser does not need. No (or a vague) Content-Type -> Turtle.
function mediaType(contentTypeHeader) {
  const type = contentTypeHeader ? contentTypeHeader.split(';')[0].trim().toLowerCase() : ''
  if (UNTYPED_CONTENT_TYPES.includes(type)) return DEFAULT_CONTENT_TYPE
  return type
}

// Turn an HTTP error answer into a readable Error and throw it (as a rejected Promise).
// FastAPI answers errors with JSON like { "detail": "Unknown endpoint: x" }; reading
// that error body as JSON is fine, it is not RDF. Other servers: the status text.
function throwHttpError(response, url) {
  const statusText = `${response.status} ${response.statusText}`.trim()
  const type = mediaType(response.headers.get('Content-Type'))

  if (type !== 'application/json') {
    return Promise.reject(new Error(`Could not load ${url}: ${statusText}`))
  }

  return response
    .json()
    .catch(() => ({})) // the body was not valid JSON after all: use the status text
    .then((body) => {
      const reason = body && body.detail ? errorText(body.detail) : statusText
      throw new Error(`Could not load ${url}: ${reason}`)
    })
}
