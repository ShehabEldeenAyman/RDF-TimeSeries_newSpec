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
//   // Fetch a document from our own server and parse it. The base IRI is the URL we
//   // fetched from, unless we pass the document's real IRI.
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
//   // A POST whose answer is Turtle (or an error as JSON): read it like loadRdf does
//   fetch(url, { method: 'POST', body: shapes })
//     .then((response) => readRdfAnswer(response, url))
//     .then(({ store, prefixes, text }) => ...)
//
//   // Your own metadata document (a user endpoint), read by the browser only:
//   fetchRdfDocument('https://example.org/my_series_tss2.ttl')   // from the web, by this browser
//   readRdfFile(file, 'file:///uploaded/my_series_tss2.ttl')      // a File picked with <input type="file">
//     .then(({ store, prefixes, text, baseIRI, contentType }) => ...)

import { rdfParser } from 'rdf-parse'
import { Store, Writer } from 'n3'
import { Readable } from 'readable-stream'
import { errorText } from './config.js'

// The syntax we assume when a server does not say what it sent. Many simple servers
// (for example raw files on GitHub) send Turtle as text/plain or as plain bytes.
const DEFAULT_CONTENT_TYPE = 'text/turtle'
const UNTYPED_CONTENT_TYPES = ['', 'text/plain', 'application/octet-stream']

// The RDF syntaxes a user's own metadata document may have (the same list as the backend's
// documents.RDF_FORMATS). application/x-turtle is an old name for Turtle.
const USER_DOCUMENT_TYPES = ['text/turtle', 'application/n-triples', 'text/n3', 'application/rdf+xml']

// What the browser asks a web server for when it fetches a user's metadata document: Turtle
// first, but the other syntaxes we read are fine too. An Accept header this short is
// "CORS-safelisted": the browser sends the request straight away, without first asking the
// server for permission (a "preflight" OPTIONS request, which some hosts refuse).
export const RDF_ACCEPT = 'text/turtle, application/rdf+xml;q=0.8, application/n-triples;q=0.8, text/n3;q=0.5, */*;q=0.1'

// Why we don't read JSON-LD documents: the parser would download whatever its @context names
const JSON_LD_REFUSED = 'JSON-LD is not read here: its @context can make the parser download other ' +
  'addresses. Use Turtle, N-Triples, N3 or RDF/XML instead.'

// The syntax of an uploaded file, by the end of its name (the browser does not know the type
// of a .ttl file: it gives the type "")
const FILE_EXTENSIONS = {
  '.ttl': 'text/turtle',
  '.nt': 'application/n-triples',
  '.n3': 'text/n3',
  '.rdf': 'application/rdf+xml',
  '.owl': 'application/rdf+xml',
  '.xml': 'application/rdf+xml',
}

// Fetch an RDF document and parse it into a new N3 Store.
//   url:     where to fetch it, e.g. '/api/catalog' (a path on our own server is fine)
//   baseIRI: the IRI of the document itself. Relative IRIs in the document, like
//            <../data/waterinfo_289423042.csv>, are resolved against it. Leave it out
//            to use the URL we fetch from.
// Gives a Promise of { store, prefixes, text, url, baseIRI, contentType }:
//   store       the quads, in an N3 Store
//   prefixes    the @prefix lines of the document, e.g. { tss: 'https://w3id.org/tss#' }
//   text        the document text exactly as the server sent it (to show it)
//   url         the absolute URL we fetched from
//   baseIRI     the base IRI the relative IRIs were resolved against
//   contentType the syntax it was parsed as, e.g. 'text/turtle'
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
        return { store: parsed.store, prefixes: parsed.prefixes, text: text, url: absoluteUrl, baseIRI: base, contentType: contentType }
      })
    })
}

// Fetch a user's own metadata document from the web, here in the browser (the demo's server
// never sees it), and parse it into a new N3 Store. Gives a Promise of
// { store, prefixes, text, url, baseIRI, contentType }, like loadRdf.
//   url: the address the user typed. It is also the document's base IRI (also when the server
//        redirects us elsewhere), so <../data/x.csv> means the same as for the document's author.
// The server must allow a browser to read its files (CORS). When it does not, the browser only
// says "Failed to fetch", so the message says what the user can do instead.
export function fetchRdfDocument(url) {
  let contentType = DEFAULT_CONTENT_TYPE

  // cache: 'no-store': always ask the server again, so a document that was just edited is read
  // as it is now. Only the Accept header is set (see RDF_ACCEPT).
  return fetch(url, { cache: 'no-store', headers: { Accept: RDF_ACCEPT } })
    .catch((err) => {
      throw new Error(`Your browser could not download ${url} (${err.message}). This host does not let a browser ` +
        'download the file (it does not allow CORS), or the address can not be reached. Download the file and ' +
        'upload it here instead ("From your computer").')
    })
    .then((response) => {
      if (!response.ok) throw new Error(`Could not load ${url}: ${response.status} ${response.statusText}`.trim())
      contentType = userDocumentType(response.headers.get('Content-Type'), url)
      return response.text()
    })
    .then((text) => {
      return parseRdf(text, contentType, url).then((parsed) => {
        return { store: parsed.store, prefixes: parsed.prefixes, text: text, url: url, baseIRI: url, contentType: contentType }
      })
    })
}

// Read a metadata file the user picked on their computer (a File from <input type="file">),
// and parse it into a new N3 Store. Nothing is sent anywhere: the browser reads the file itself.
//   baseIRI: the document's IRI: where it is published, or a made-up file:///uploaded/<name>
// Gives a Promise of { store, prefixes, text, url: null, baseIRI, contentType }.
export function readRdfFile(file, baseIRI) {
  let contentType
  try {
    contentType = fileType(file.name)
  } catch (err) {
    return Promise.reject(err)
  }
  return file.text().then((text) => {
    return parseRdf(text, contentType, baseIRI).then((parsed) => {
      return { store: parsed.store, prefixes: parsed.prefixes, text: text, url: null, baseIRI: baseIRI, contentType: contentType }
    })
  })
}

// The syntax of a user's document, from the Content-Type its server sent. Throws an Error for
// JSON-LD, an HTML page, and any other type that is not an RDF syntax we read.
//   'text/turtle; charset=utf-8' -> 'text/turtle'
//   'application/octet-stream, application/octet-stream' (Zenodo sends it twice) -> 'text/turtle'
function userDocumentType(contentTypeHeader, url) {
  const header = contentTypeHeader ? contentTypeHeader : ''
  const type = mediaType(header.split(',')[0]) // only the first of a doubled value
  if (type === 'application/x-turtle') return 'text/turtle'
  if (type === 'application/ld+json') throw new Error(`${url} is JSON-LD. ${JSON_LD_REFUSED}`)
  if (type === 'text/html') {
    throw new Error(`${url} is an HTML page, not RDF. Is it the address of the file itself (for example its "raw" link)?`)
  }
  if (!USER_DOCUMENT_TYPES.includes(type)) {
    throw new Error(`${url} is ${type}, which is not an RDF syntax this dashboard reads (Turtle, RDF/XML, N-Triples or N3)`)
  }
  return type
}

// The syntax of an uploaded file, by the end of its name: 'my_series.ttl' -> 'text/turtle'
function fileType(fileName) {
  const name = fileName.toLowerCase()
  if (name.endsWith('.jsonld') || name.endsWith('.json')) throw new Error(`${fileName} is JSON-LD. ${JSON_LD_REFUSED}`)
  for (const extension of Object.keys(FILE_EXTENSIONS)) {
    if (name.endsWith(extension)) return FILE_EXTENSIONS[extension]
  }
  throw new Error(`${fileName}: the name must end in .ttl (Turtle), .nt (N-Triples), .n3 (N3) or .rdf, .owl, .xml (RDF/XML)`)
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
// (Used for the answer of a SHACL test with uploaded shapes, see dataApi.js.)
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
