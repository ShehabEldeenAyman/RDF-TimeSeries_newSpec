// userEndpoints.js: your own metadata endpoints, kept in this browser tab only.
//
// Besides the bundled endpoints (the demo's server lists them in its catalogue), you can add your
// own: a metadata document on the web (by its address) or a file on your computer (an upload).
// The browser reads the document itself and keeps it in memory, in App.jsx's state; the demo's
// server never sees it, nor its data (see dataApi.js and browser/dataSource.js).
//
// Only the web addresses of your endpoints are remembered, in sessionStorage: the storage of this
// browser tab, which the browser empties when the tab is closed. So a reload of the page adds them
// again (downloading them again). Uploaded files are not stored anywhere: a reload forgets them.
//
// Usage:
//   loadFromUrl('https://example.org/my_series_tss2.ttl').then((document) => ...)
//   loadFromFile(file, '').then((document) => ...)   // '' or the address where it is published
//   const endpoint = makeUserEndpoint(1, document, { url: null, fileName: file.name, dataFiles: [] })
//   storeUrls(['https://...']) ... storedUrls()      // the remembered addresses

import { fetchRdfDocument, readRdfFile } from './rdf'
import { findSource, oneText, RDFS_LABEL } from './tss'

// The name of the remembered list in sessionStorage
const STORAGE_KEY = 'tss2-dashboard-endpoint-urls'

// Fetch a metadata document from the web, in the browser, and check it.
// Gives a Promise of the parsed document ({ store, prefixes, text, baseIRI, contentType, ... }).
export function loadFromUrl(url) {
  return fetchRdfDocument(url).then((document) => checkDocument(document, url))
}

// Read a metadata file from your computer, and check it.
//   publishedAt: where the document is published on the web, or '' when it is not. It is the
//                document's base IRI: relative IRIs in it, like <../data/x.csv>, are read
//                against it. Without one the file gets a made-up IRI, file:///uploaded/<name>.
// Gives a Promise of the parsed document.
export function loadFromFile(file, publishedAt) {
  let base = 'file:///uploaded/' + encodeURIComponent(file.name)
  if (publishedAt !== '') base = publishedAt
  return readRdfFile(file, base).then((document) => checkDocument(document, file.name))
}

// Every tab is about the one tss:Source of the document, so it must have exactly one
function checkDocument(document, name) {
  if (findSource(document.store) === null) {
    throw new Error(`${name} has no tss:Source, or more than one. This app needs exactly one.`)
  }
  return document
}

// A user endpoint: the same keys as a bundled one (id, iri, label, comment), plus what the
// browser needs to work with it alone.
//   number:   its number in this tab: the id is user-1, user-2, ...
//   document: the parsed document (loadFromUrl or loadFromFile)
//   how:      { url, fileName, dataFiles }: its web address (or null for an upload), the name of the
//             uploaded file (or null), and the data files picked with it ([] when none)
export function makeUserEndpoint(number, document, how) {
  const label = oneText(document.store, findSource(document.store), RDFS_LABEL)
  let name = how.fileName
  if (how.url !== null) name = how.url
  return {
    id: 'user-' + number,
    iri: document.baseIRI,               // the document's IRI (its base IRI)
    label: label !== '' ? label : name,  // the source's rdfs:label, else its address or file name
    comment: null,
    user: true,                          // read by the browser only (see dataApi.js)
    url: how.url,
    fileName: how.fileName,
    dataFiles: how.dataFiles,
    contentType: document.contentType,   // its syntax, e.g. 'text/turtle'
    document: document,                  // the parsed document, so App.jsx does not read it again
  }
}

// The remembered web addresses (a list of texts, each address once), or [] when there are none.
// sessionStorage can be switched off (private windows, strict settings): then nothing is remembered.
export function storedUrls() {
  try {
    const text = window.sessionStorage.getItem(STORAGE_KEY)
    if (text === null) return []
    const list = JSON.parse(text) // a plain list of addresses (not RDF)
    if (!Array.isArray(list)) return []
    return withoutDuplicates(list.filter((url) => typeof url === 'string'))
  } catch {
    return []
  }
}

// Remember these web addresses for a reload of this tab (each address once)
export function storeUrls(urls) {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(withoutDuplicates(urls)))
  } catch {
    // sessionStorage is switched off: a reload will not add them again, that is all
  }
}

// The same list, with every text only once (in the order they first appear). A Set keeps each
// value once; Array.from makes it a list again.
function withoutDuplicates(list) {
  return Array.from(new Set(list))
}
