// dataSource.js: where the data of a user endpoint comes from, here in the browser tab.
//
// A user endpoint (a metadata file you uploaded, or one added by its web address) is read by the
// browser only: neither the metadata nor the data ever goes to the demo's server. The readings are
//   - embedded in the metadata (tss:embedded true, tss:points): App.jsx already read them as RDF
//     (rdf.points), so no file is needed, or
//   - in a data file (tss:dataFile). findDataFile finds that file's contents, as a Blob, in this order:
//       1. a file you picked in a data tab ("Pick <name> from your computer", "Pick another file")
//       2. else one of the data files you uploaded with the metadata, matched by its name: the
//          last part of the tss:dataFile IRI (for <../data/waterinfo_289423042.csv> that is
//          waterinfo_289423042.csv), also when only upper and lower case differ (see dataFileName)
//       3. else, for a web address (http:// or https://), the browser downloads it itself,
//          once per endpoint. That only works when the host allows it (CORS).
//       4. else (a file: address, or a download that failed) it can't be read: the data tabs
//          ask you to pick the file from your computer. When you uploaded exactly one data file
//          with the metadata and its name matched nothing, they offer that one ("Use <name>").
//     A file from your computer (1 and 2) is checked first: does it fit the metadata? For a CSV
//     file with a header line, that line must name every field's tss:columnName (see checkFit).
//     When it does not, the data tabs say which columns are missing, and the file is used only
//     when you say so ("Use <name> anyway"): wrong data is never shown without a word.
//
// Usage:
//   import { findDataFile } from './browser/dataSource.js'
//
//   findDataFile({ endpoint, rdf, picked })
//     .then((found) => ...)   // null for embedded points, else { blob, name, origin, iri, note, warning }:
//                             //   blob     the file's contents (a File or Blob)
//                             //   name     its file name, e.g. 'waterinfo_289423042.csv'
//                             //   origin   'picked' (from your computer) or 'fetched' (downloaded by the browser)
//                             //   iri      the tss:dataFile IRI
//                             //   note     something to say about the file, or null
//                             //   warning  something to warn about (a picked file with another name), or null
//     .catch((error) => ...)  // error.message says why. What the data tabs can offer:
//                             //   error.missingFile  the name of the file to pick from your computer
//                             //   error.misfit       the File that does not fit the metadata ("Use it anyway")
//                             //   error.offerFile    the one data file you uploaded, whose name matched nothing ("Use it")

import { one, all, booleanValue, isEmbedded, TSS_CSV, TSS_DATA_FILE, TSS_DELIMITER, TSS_ENCODING, TSS_FORMAT,
  TSS_HAS_FIELD, TSS_HEADER, TSS_COLUMN_NAME, TSS_PARQUET } from '../tss.js'

// The data files the browser downloaded: endpoint id + IRI -> a Promise of the Blob. So a file is
// downloaded once per endpoint, also when several tabs ask for it at the same time.
// (Only in this tab's memory; forgetDataFiles empties it when an endpoint is removed.)
const downloads = new Map()

// Words that some download addresses end with instead of a file name: Zenodo's API ends in
// .../files/x.csv/content, other sites in /download or /raw. The file name is the part before.
const GENERIC_NAMES = ['content', 'download', 'raw', 'file', 'data']

// How much of a CSV file checkFit reads to find its first line (a header line is much shorter)
const HEADER_BYTES = 65536

// The file name at the end of an IRI, with %20 and the like decoded:
//   'https://example.org/data/my%20series.csv'                     -> 'my series.csv'
//   'https://zenodo.org/api/records/1/files/series.csv/content'   -> 'series.csv' (see GENERIC_NAMES)
export function dataFileName(iri) {
  let path = iri
  try {
    path = new URL(iri).pathname // without ?query and #fragment
  } catch {
    // not a full IRI: use the text as it is
  }
  const parts = path.split('/')
  let name = parts[parts.length - 1]
  // From the end, skip the generic words (and an empty last part, after a last "/")
  for (let index = parts.length - 1; index >= 0; index--) {
    const part = parts[index]
    if (part !== '' && !GENERIC_NAMES.includes(part.toLowerCase())) {
      name = part
      break
    }
  }
  try {
    return decodeURIComponent(name)
  } catch {
    return name // a stray % that is no escape: keep the name as it is
  }
}

// Can the browser download this IRI? Only web addresses (not file:, urn:, ...)
export function isWebAddress(iri) {
  return iri.startsWith('http://') || iri.startsWith('https://')
}

// Two file names that are the same, apart from upper and lower case ('Data.CSV', 'data.csv')
function sameName(a, b) {
  return a.toLowerCase() === b.toLowerCase()
}

// An Error that says the data file is missing, and which file to pick
function missingFileError(message, name) {
  const error = new Error(message)
  error.missingFile = name
  return error
}

// The data file you uploaded with the metadata whose name is the one the metadata names: the
// exact name first, else a name that differs only in upper and lower case (when one file has it)
function matchingUpload(dataFiles, name) {
  for (const file of dataFiles) {
    if (file.name === name) return file
  }
  const sameLetters = dataFiles.filter((file) => sameName(file.name, name))
  if (sameLetters.length === 1) return sameLetters[0]
  return null
}

// The contents of the data file of a user endpoint. Gives a Promise (see the top of this file).
//   endpoint: the user endpoint (App.jsx): endpoint.id, endpoint.dataFiles (the files uploaded with
//             the metadata, or [])
//   rdf:      its parsed metadata ({ store, source, ... })
//   picked:   the file picked in a data tab, or null: { file, anyway }. anyway is true when you
//             said "Use it anyway" for a file that does not fit the metadata.
export function findDataFile({ endpoint, rdf, picked }) {
  // Embedded points: no data file
  if (isEmbedded(rdf.store, rdf.source) === true) return Promise.resolve(null)

  const dataFile = one(rdf.store, rdf.source, TSS_DATA_FILE)
  if (dataFile === null) return Promise.reject(new Error('The source has no tss:dataFile.'))
  const iri = dataFile.value
  const name = dataFileName(iri)

  // 1. A file you picked in a data tab, for this endpoint
  if (picked) {
    let warning = null
    if (!sameName(picked.file.name, name)) {
      warning = `The metadata names ${name}: check that this is the right file.`
    }
    return takeOwnFile(picked.file, 'The file you picked,', { rdf: rdf, iri: iri, name: name, anyway: picked.anyway, warning: warning })
  }

  // 2. One of the data files you uploaded with the metadata, with the right name
  const upload = matchingUpload(endpoint.dataFiles, name)
  if (upload !== null) {
    return takeOwnFile(upload, 'The data file you uploaded with the metadata,', { rdf: rdf, iri: iri, name: name, anyway: false, warning: null })
  }

  // The one data file you uploaded, when its name matched nothing: offered when the file can't be
  // downloaded (it is not used without asking: it may be another file)
  let offer = null
  if (endpoint.dataFiles.length === 1 && all(rdf.store, rdf.source, TSS_DATA_FILE).length === 1) {
    offer = endpoint.dataFiles[0]
  }

  // 3. A web address: the browser downloads it
  if (isWebAddress(iri)) {
    let note = null
    if (endpoint.dataFiles.length > 0) note = `None of the data files you uploaded is called ${name}.`
    return download(endpoint.id, iri, name)
      .then((blob) => ({ blob: blob, name: name, origin: 'fetched', iri: iri, note: note, warning: null }))
      .catch((error) => {
        throw offer !== null ? offerError(error.message, name, offer) : error
      })
  }

  // 4. Nothing to download (a file: address, ...)
  let message = `The data file ${iri} is not on the web, so your browser can't download it.`
  if (endpoint.dataFiles.length > 0) {
    message += ` None of the data files you uploaded is called ${name}.`
  }
  if (offer !== null) return Promise.reject(offerError(message, name, offer))
  return Promise.reject(missingFileError(message + ` Pick ${name} from your computer.`, name))
}

// The Error of a data file that can't be read, which offers the one data file you uploaded
function offerError(message, name, file) {
  const error = missingFileError(`${message} You uploaded one data file with the metadata, ${file.name}: ` +
    `use that one, or pick ${name} from your computer.`, name)
  error.offerFile = file
  return error
}

// A data file from your computer: check that it fits the metadata (checkFit), then use it.
// When it does not fit, the Promise is rejected with error.misfit (the data tabs then offer
// "Use it anyway" and "Pick another file"), unless you already said to use it anyway.
//   file: the File;  what: how the message starts, e.g. 'The file you picked,'
//   about: { rdf, iri, name, anyway, warning }: the metadata, the tss:dataFile IRI and its file
//          name, "use it anyway", and a warning to pass on
function takeOwnFile(file, what, about) {
  return checkFit(file, about.rdf)
    .catch((err) => {
      // The browser could not read the file (moved or deleted since it was picked?)
      throw missingFileError(`Could not read ${file.name} (${err.message}). Pick it again, or another file.`, about.name)
    })
    .then((fit) => takeIfFits(file, what, about, fit))
}

// The found data file, or the Error of a file that does not fit (see takeOwnFile)
function takeIfFits(file, what, about, fit) {
  let warning = about.warning
  if (fit.missing.length > 0) {
    const columns = columnList(fit.missing)
    if (!about.anyway) {
      const error = missingFileError(`${what} ${file.name}, does not fit the metadata: its first line has no ` +
        `column ${columns}. With tss:header true, the first line must name the tss:columnName of every field. ` +
        'Pick another file, or use this one anyway.', about.name)
      error.misfit = file
      throw error
    }
    warning = `Its first line has no column ${columns}: you chose to use it anyway.`
  }
  return { blob: file, name: file.name, origin: 'picked', iri: about.iri, note: fit.note, warning: warning }
}

// '"Value", "Timestamp"' (at most 10 names, and how many more)
function columnList(names) {
  let text = names.slice(0, 10).map((name) => `"${name}"`).join(', ')
  if (names.length > 10) text += ` and ${names.length - 10} more`
  return text
}

// Does a data file from your computer fit the metadata? Gives a Promise of { missing, note }:
//   missing  the fields' column names (tss:columnName) the file's header line does not have
//            ([] when it fits, or when it can't be checked here)
//   note     why the columns were not checked, or null
// Only a CSV file with a header line is checked, and only its first line is read (it can be
// big). Without a header line the columns are found by tss:columnIndex; a CSV setting the
// dashboard can't read (another encoding, a longer delimiter) is reported by the data tabs.
function checkFit(file, rdf) {
  const store = rdf.store
  const source = rdf.source
  const format = one(store, source, TSS_FORMAT)
  if (format !== null && format.value === TSS_PARQUET) {
    // A Parquet file's column names are in its metadata at the end of the file; only DuckDB
    // reads that here, and it starts in the data tabs. A missing column is reported there.
    return Promise.resolve({ missing: [], note: 'Its columns are not checked before it is used (that needs DuckDB, which reads Parquet files).' })
  }
  if (format === null || format.value !== TSS_CSV) return Promise.resolve({ missing: [], note: null })
  if (booleanValue(one(store, source, TSS_HEADER)) === false) return Promise.resolve({ missing: [], note: null })

  // The defaults are the backend's: a comma, and UTF-8
  const delimiterTerm = one(store, source, TSS_DELIMITER)
  const encodingTerm = one(store, source, TSS_ENCODING)
  const delimiter = delimiterTerm !== null ? delimiterTerm.value : ','
  const encoding = encodingTerm !== null ? encodingTerm.value : 'utf-8'
  if (Array.from(delimiter).length !== 1 || !['utf-8', 'utf8'].includes(encoding.toLowerCase())) {
    return Promise.resolve({ missing: [], note: null })
  }

  // The column names the metadata expects: one per field that has a tss:columnName
  const expected = []
  for (const field of all(store, source, TSS_HAS_FIELD)) {
    const column = one(store, field, TSS_COLUMN_NAME)
    if (column !== null && !expected.includes(column.value)) expected.push(column.value)
  }

  return file.slice(0, HEADER_BYTES).arrayBuffer().then((buffer) => {
    // ignoreBOM: a byte order mark stays part of the first name, as when the tabs read the file
    const text = new TextDecoder('utf-8', { ignoreBOM: true }).decode(buffer)
    const header = firstCsvLine(text, delimiter)
    const missing = expected.filter((name) => !header.includes(name))
    return { missing: missing, note: null }
  })
}

// The cells of the first line of CSV text, read like Python's csv module (which the backend uses)
// reads them: a cell ends at the delimiter, the line at the first line break outside quotes, and a
// cell that starts with " is quoted: it may hold the delimiter, and "" in it is one ".
function firstCsvLine(text, delimiter) {
  const cells = []
  let cell = ''
  let quoted = false  // inside a quoted part
  let atStart = true  // at the start of a cell (only there does " start a quoted part)
  for (let i = 0; i < text.length; i++) {
    const character = text[i]
    if (quoted) {
      if (character === '"' && text[i + 1] === '"') {
        cell += '"' // "" inside quotes: one "
        i += 1
      } else if (character === '"') {
        quoted = false // the end of the quoted part
      } else {
        cell += character
      }
    } else if (character === '"' && atStart) {
      quoted = true
      atStart = false
    } else if (character === delimiter) {
      cells.push(cell)
      cell = ''
      atStart = true
    } else if (character === '\n' || character === '\r') {
      break // the end of the first line
    } else {
      cell += character
      atStart = false
    }
  }
  cells.push(cell)
  return cells
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
