// dataSource.js: where the data of a user endpoint comes from, here in the browser tab.
//
// A user endpoint (a metadata file you uploaded, or one added by its web address) is read by the
// browser only: neither the metadata nor the data ever goes to the demo's server. The readings are
//   - embedded in the metadata (tss:embedded true, tss:points): App.jsx already read them as RDF
//     (rdf.points), so no file is needed, or
//   - in a data file (tss:dataFile). findDataFile finds that file's contents, as a Blob:
//       1. a file you picked from your computer: in a data tab ("Pick <name> from your computer"),
//          or with the metadata in the upload form, matched by the last part of the tss:dataFile
//          IRI (for <../data/waterinfo_289423042.csv> that is waterinfo_289423042.csv)
//       2. else, for a web address (http:// or https://), the browser downloads it itself,
//          once per endpoint. That only works when the host allows it (CORS).
//       3. else (a file: address, or a download that failed) it can't be read: the data tabs
//          ask you to pick the file from your computer.
//
// Usage:
//   import { findDataFile } from './browser/dataSource.js'
//
//   findDataFile({ endpoint, rdf, pickedFile })
//     .then((found) => ...)   // null for embedded points, else { blob, name, origin, iri }:
//                             //   blob    the file's contents (a File or Blob)
//                             //   name    its file name, e.g. 'waterinfo_289423042.csv'
//                             //   origin  'picked' (from your computer) or 'fetched' (downloaded by the browser)
//                             //   iri     the tss:dataFile IRI
//     .catch((error) => ...)  // error.message says why; error.missingFile is the name of the file to pick

import { one, isEmbedded, TSS_DATA_FILE } from '../tss.js'

// The data files the browser downloaded: endpoint id + IRI -> a Promise of the Blob. So a file is
// downloaded once per endpoint, also when several tabs ask for it at the same time.
// (Only in this tab's memory; forgetDataFiles empties it when an endpoint is removed.)
const downloads = new Map()

// The file name at the end of an IRI, with %20 and the like decoded:
//   'https://example.org/data/my%20series.csv' -> 'my series.csv'
export function dataFileName(iri) {
  let path = iri
  try {
    path = new URL(iri).pathname // without ?query and #fragment
  } catch {
    // not a full IRI: use the text as it is
  }
  const last = path.slice(path.lastIndexOf('/') + 1)
  try {
    return decodeURIComponent(last)
  } catch {
    return last // a stray % that is no escape: keep the name as it is
  }
}

// Can the browser download this IRI? Only web addresses (not file:, urn:, ...)
export function isWebAddress(iri) {
  return iri.startsWith('http://') || iri.startsWith('https://')
}

// An Error that says the data file is missing, and which file to pick
function missingFileError(message, name) {
  const error = new Error(message)
  error.missingFile = name
  return error
}

// The contents of the data file of a user endpoint. Gives a Promise (see the top of this file).
//   endpoint:   the user endpoint (App.jsx): endpoint.id, endpoint.dataFiles (the files picked with
//               the metadata in the upload form, or [])
//   rdf:        its parsed metadata ({ store, source, ... })
//   pickedFile: the file picked in a data tab, or null. It is used whatever its name.
export function findDataFile({ endpoint, rdf, pickedFile }) {
  // Embedded points: no data file
  if (isEmbedded(rdf.store, rdf.source) === true) return Promise.resolve(null)

  const dataFile = one(rdf.store, rdf.source, TSS_DATA_FILE)
  if (dataFile === null) return Promise.reject(new Error('The source has no tss:dataFile.'))
  const iri = dataFile.value
  const name = dataFileName(iri)

  // 1. A file you picked in a data tab, for this endpoint
  if (pickedFile) {
    return Promise.resolve({ blob: pickedFile, name: pickedFile.name, origin: 'picked', iri: iri })
  }
  // ... or one of the data files you picked with the metadata, with the right name
  for (const file of endpoint.dataFiles) {
    if (file.name === name) return Promise.resolve({ blob: file, name: file.name, origin: 'picked', iri: iri })
  }

  // 2. A web address: the browser downloads it
  if (isWebAddress(iri)) {
    return download(endpoint.id, iri, name).then((blob) => ({ blob: blob, name: name, origin: 'fetched', iri: iri }))
  }

  // 3. Nothing to download (a file: address, ...)
  let message = `The data file ${iri} is not on the web, so your browser can't download it.`
  if (endpoint.dataFiles.length > 0) {
    message += ` None of the data files you picked is called ${name}.`
  }
  return Promise.reject(missingFileError(message + ` Pick ${name} from your computer.`, name))
}

// Download a data file in the browser, once per endpoint (see downloads)
function download(endpointId, iri, name) {
  const key = endpointId + ' ' + iri
  if (downloads.has(key)) return downloads.get(key)

  // cache: 'no-store': always the file as it is now. No extra headers, so the browser sends the
  // request straight away (a header of our own would need the host's permission first).
  const promise = fetch(iri, { cache: 'no-store' })
    // fetch fails without an answer: the host does not allow CORS, or can't be reached (or
    // the page is https and the file http: browsers block that too)
    .catch((err) => {
      let why = 'This host does not let a browser download the file (it does not allow CORS), or it can not be reached.'
      if (window.location.protocol === 'https:' && iri.startsWith('http:')) {
        why = 'A page opened with https:// may not download an http:// file (mixed content).'
      }
      throw missingFileError(`Your browser could not download the data file ${iri} (${err.message}). ${why} ` +
        `Download the file, then pick ${name} from your computer here.`, name)
    })
    .then((response) => {
      if (!response.ok) {
        const status = `${response.status} ${response.statusText}`.trim()
        throw missingFileError(`Could not download the data file ${iri}: ${status}. Pick ${name} from your computer instead.`, name)
      }
      return response.blob()
    })

  downloads.set(key, promise)
  // A failed download is not kept: the next try (e.g. when the endpoint is opened again) downloads again
  promise.catch(() => downloads.delete(key))
  return promise
}

// Forget the downloaded data files of an endpoint (when it is removed), so their memory is freed
export function forgetDataFiles(endpointId) {
  for (const key of Array.from(downloads.keys())) {
    if (key.startsWith(endpointId + ' ')) downloads.delete(key)
  }
}
