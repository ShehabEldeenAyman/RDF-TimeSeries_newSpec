// dataApi.js: every question a tab asks about the data of an endpoint, in one object.
//
// There are two kinds of endpoint, and they get their answers in two different places:
//   - a bundled endpoint (a .ttl file in "metadata endpoint/"): the demo's server reads its data
//     and computes the answers (the /api routes, see code/webapp/backend/)
//   - your own endpoint (uploaded, or added by web address): the answers are computed here, in
//     this browser tab (the modules in src/browser/), so its data never goes to the server
// makeDataApi gives the same object, with the same methods and the same answers, for both. The
// tabs only call this object, so they don't need to know which kind of endpoint they show, and
// for your own endpoints no tab ever calls /api.
//
// Usage (App.jsx makes one per endpoint, and every tab gets it as its "api" prop):
//   const api = makeDataApi(endpoint, rdf, pickedFile)
//
//   api.dataPreview().then(({ found, note, text }) => ...)   // the first lines of the data file
//   api.dataFileInfo().then((info) => ...)                   // { downloadUrl, fileName, origin, iri }
//   api.rows(10).then(({ columns, rows }) => ...)            // the first rows (series inspector)
//   api.filterColumns(fieldIri).then((list) => ...)          // [{ field, values }] (query form)
//   api.query(params).then((answer) => ...)                  // the query form's readings and summary
//   api.sqlTables().then((schema) => ...)                    // the SQL box's tables and examples
//   api.sql('SELECT ...').then((result) => ...)              // one SQL query
//   api.generateRdf().then((result) => ...)                  // SOSA observations: counts, downloadUrl,
//                                                            // and result.preview() (parsed Turtle)
//   api.shacl(null, null).then(({ store, prefixes, text }) => ...)        // the report, default shapes
//   api.shacl(shapesText, 'my_shapes.ttl').then(...)                        // ... or your own shapes
//   api.close()   // when the endpoint is left: stops DuckDB and the RDF generation, frees the
//                 // downloads (browser only). A closed api answers nothing any more.
//   api.inBrowser // true for your own endpoint (the answers are computed in this tab)
//
// Every method gives a Promise. A problem rejects it with an Error whose message says why: the
// server's "detail", or the same text computed in the browser. (So: .catch((err) => setError(err.message)))

import { API_URL, errorText, fetchErrorText } from './config'
import { loadRdf, parseRdf, readRdfAnswer } from './rdf'
import { findDataFile } from './browser/dataSource'
import { dataPreview, firstRows, filterColumns, querySeries } from './browser/query'
import { sqlTables, runSql } from './browser/sqlBox'
import { generateObservations } from './browser/observations'
import { validateInBrowser } from './browser/shacl'

// Has the SHACL check run in this tab before? The first time, the browser loads Python
// (Pyodide) first, which takes a few seconds; the Shacl tab says so.
let shaclReady = false

// Every api object gets a number of its own (api.key), so React can tell a new one from the old
// one: App.jsx draws the tabs again for a new api (key={api.key})
let lastKey = 0

// The api object for an endpoint.
//   endpoint:   a bundled endpoint (from the catalogue) or a user endpoint (endpoint.user is true)
//   rdf:        its parsed metadata, the "rdf" prop of every tab (see App.jsx)
//   pickedFile: only for a user endpoint: the data file picked in a data tab, or null
export function makeDataApi(endpoint, rdf, pickedFile) {
  const api = endpoint.user ? browserApi(endpoint, rdf, pickedFile) : serverApi(endpoint)
  lastKey += 1
  api.key = lastKey
  return api
}

// --- A bundled endpoint: the server answers ------------------------------------------

function serverApi(endpoint) {
  const base = `${API_URL}/endpoints/${encodeURIComponent(endpoint.id)}`
  // The full address of the SHACL route. It is also the base IRI for the report and for
  // uploaded shapes (the backend reads relative IRIs in uploaded shapes against it too).
  const shaclUrl = new URL(`${base}/shacl`, window.location.href).href

  return {
    inBrowser: false,

    // GET /data: { found, note, text }
    dataPreview() {
      return getJson(`${base}/data`)
    },

    // The download link of the original data file: a normal link is enough (no fetch), the
    // backend answers with the file and tells the browser to save it
    dataFileInfo() {
      return Promise.resolve({ downloadUrl: `${base}/data/download`, fileName: null, origin: 'server', iri: null })
    },

    // GET /rows: { columns: [field IRIs], rows: [[text or null]] }
    rows(limit) {
      return getJson(`${base}/rows?limit=${limit}`)
    },

    // GET /filter-columns: [{ field, values }]
    filterColumns(fieldIri) {
      const params = new URLSearchParams({ field: fieldIri })
      return getJson(`${base}/filter-columns?${params}`)
    },

    // The address of a query, to show it under the answer (URLSearchParams writes "field=...&...")
    queryAddress(params) {
      return `${base}/query?${params}`
    },

    // GET /query: the readings, the summary and the gaps
    query(params) {
      return getJson(`${base}/query?${params}`)
    },

    // GET /sql/tables: the tables, their columns, and example queries
    sqlTables() {
      return getJson(`${base}/sql/tables`)
    },

    // POST /sql, with the query in the body as JSON (a query can be too long for the address)
    sql(text) {
      return getJson(`${base}/sql`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sql: text }),
      })
    },

    // POST /rdf: the counts, and the addresses of the preview (Turtle) and of the download
    generateRdf() {
      return getJson(`${base}/rdf`, { method: 'POST' }).then((answer) => {
        // preview(): fetch the preview and parse it (a Promise of { store, prefixes })
        return Object.assign({}, answer, { preview: () => loadRdf(answer.previewUrl) })
      })
    },

    shaclBase: shaclUrl,
    shaclStarting() {
      return false // the server is always ready
    },

    // GET /shacl (default shapes) or POST /shacl (your shapes as the body): the report as Turtle,
    // parsed. Gives a Promise of { store, prefixes, text }.
    shacl(shapesText) {
      if (shapesText === null) return loadRdf(shaclUrl)
      return fetch(shaclUrl, { method: 'POST', headers: { 'Content-Type': 'text/turtle' }, body: shapesText })
        // fetch fails only when there is no answer at all (the backend is not running)
        .catch((err) => {
          throw new Error(fetchErrorText(err))
        })
        // The report as Turtle, or FastAPI's error as JSON: { "detail": "..." } (see readRdfAnswer in rdf.js)
        .then((response) => readRdfAnswer(response, shaclUrl))
    },

    close() {
      // nothing to stop: the server keeps no state for the page
    },
  }
}

// Fetch a JSON answer of the backend. FastAPI puts its error message in "detail": that becomes a
// rejected Promise with that message, like a failed fetch (the backend is not running).
function getJson(url, options) {
  return fetch(url, options)
    .catch((err) => {
      throw new Error(fetchErrorText(err))
    })
    .then((response) => {
      // An answer that is not JSON did not come from the backend. Usually the backend has
      // stopped, and Vite answers "502 Bad Gateway" with an empty body: say so, with the status.
      return response.json().catch(() => {
        throw new Error(fetchErrorText(new Error(`${response.status} ${response.statusText}`.trim())))
      })
    })
    .then((answer) => {
      if (answer && answer.detail) throw new Error(errorText(answer.detail))
      return answer
    })
}

// --- Your own endpoint: this browser tab answers --------------------------------------

function browserApi(endpoint, rdf, pickedFile) {
  // Each of these is made once, the first time a tab needs it (so nothing is downloaded and
  // DuckDB does not start until you open a tab that needs the data)
  let dataFilePromise = null // the data file (see dataSource.js)
  let databasePromise = null // the DuckDB database (duck.js), about 400 MB of memory
  let fileUrl = null         // a blob: address of the data file, for its download link
  let rdfUrl = null          // a blob: address of the latest generated RDF
  // close() has run: the endpoint was left or removed. A question that was still waiting (for
  // the data file to download, say) gets its answer later; from then on nothing new may start,
  // or it would never be stopped: a DuckDB (about 400 MB, with your data in it), or a blob:
  // address that keeps your data in memory until the page is reloaded.
  let closed = false
  // stopper.abort() (in close) stops the RDF generation that is still running in its Web Worker
  const stopper = new AbortController()

  // The error of a question that comes back after close(). Nobody sees it: the tabs that asked
  // are gone by then.
  function closedError() {
    return new Error('This endpoint was closed.')
  }

  // The data file: a Promise of { blob, name, origin, iri }, or of null for embedded points
  function dataFile() {
    if (dataFilePromise === null) {
      dataFilePromise = findDataFile({ endpoint: endpoint, rdf: rdf, pickedFile: pickedFile })
    }
    return dataFilePromise
  }

  // The DuckDB database of this endpoint (the data table and the metadata tables). duck.js is
  // big, so it is loaded only now, with import().
  function getDatabase() {
    if (closed) return Promise.reject(closedError())
    if (databasePromise === null) {
      databasePromise = dataFile()
        .then((found) => {
          // close() came while the data file downloaded: don't start DuckDB at all
          if (closed) throw closedError()
          return found
        })
        .then((found) => import('./browser/duck.js').then((duck) => {
          return duck.openDatabase({
            store: rdf.store,
            prefixes: rdf.prefixes,
            source: rdf.source,
            documentIri: endpoint.iri,
            dataFile: found ? found.blob : null,
            points: rdf.points,
            endpointId: endpoint.id,
          })
        }))
        .then((db) => {
          // close() came while DuckDB was starting: stop it again at once
          if (closed) {
            db.close()
            throw closedError()
          }
          return db
        })
      // A failure is not kept: the next question tries again
      const opening = databasePromise
      opening.catch(() => {
        if (databasePromise === opening) databasePromise = null
      })
    }
    return databasePromise
  }

  // What the modules of query.js need: the rdf prop, plus the data file and the database
  function data() {
    return dataFile().then((found) => {
      if (closed) throw closedError() // left while the file downloaded: nobody waits for this answer
      return Object.assign({}, rdf, { dataFile: found ? found.blob : null, getDatabase: getDatabase })
    })
  }

  return {
    inBrowser: true,

    dataPreview() {
      return data().then((d) => dataPreview(d))
    },

    // Where the data file came from, and a blob: address to download it (the browser's own copy)
    dataFileInfo() {
      return dataFile().then((found) => {
        // close() has freed the addresses already: a new one now would never be freed
        if (closed) throw closedError()
        if (found === null) return null
        if (fileUrl === null) fileUrl = URL.createObjectURL(found.blob)
        return { downloadUrl: fileUrl, fileName: found.name, origin: found.origin, iri: found.iri }
      })
    },

    rows(limit) {
      return data().then((d) => firstRows(d, limit))
    },

    filterColumns(fieldIri) {
      return data().then((d) => filterColumns(d, fieldIri))
    },

    // No address: the query is not sent anywhere
    queryAddress() {
      return null
    },

    query(params) {
      return data().then((d) => querySeries(d, params))
    },

    sqlTables() {
      return getDatabase().then((db) => sqlTables(db, rdf.store, rdf.source))
    },

    sql(text) {
      return getDatabase().then((db) => runSql(db, text))
    },

    // The SOSA observations, made in a Web Worker. The whole graph is a Blob in this tab: the
    // download link is a blob: address (the previous one is freed).
    generateRdf() {
      return dataFile()
        .then((found) => {
          if (closed) throw closedError()
          return generateObservations({
            metadataText: rdf.text,
            contentType: endpoint.contentType,
            baseIRI: endpoint.iri,
            dataFile: found ? found.blob : null,
            getDatabase: getDatabase, // only used for a Parquet file
            signal: stopper.signal,   // close() stops the worker
          })
        })
        .then((result) => {
          // close() has freed the addresses already: a new one now would keep the graph in memory
          if (closed) throw closedError()
          if (rdfUrl !== null) URL.revokeObjectURL(rdfUrl)
          rdfUrl = URL.createObjectURL(result.ntriples)
          return {
            observations: result.observations,
            triples: result.triples,
            series: result.series,
            previewObservations: result.previewObservations,
            previewTruncated: result.previewTruncated,
            bytes: result.bytes,
            filename: result.filename,
            downloadUrl: rdfUrl,
            // The preview is Turtle text already: parse it (a Promise of { store, prefixes })
            preview: () => parseRdf(result.previewTurtle, 'text/turtle', endpoint.iri),
          }
        })
    },

    // Relative IRIs in your own shapes are read against the metadata's IRI
    shaclBase: endpoint.iri,
    // Will this check load Python first? (Only the first one in this tab.)
    shaclStarting() {
      return !shaclReady
    },

    // pyshacl in this tab (Pyodide). Gives a Promise of { store, prefixes, text }, like the server.
    shacl(shapesText, shapesName) {
      return validateInBrowser({
        metadataText: rdf.text,
        contentType: endpoint.contentType,
        baseIRI: endpoint.iri,
        shapesText: shapesText,
        shapesName: shapesName,
      }).then((answer) => {
        shaclReady = true
        return parseRdf(answer.turtle, 'text/turtle', endpoint.iri).then((parsed) => {
          return { store: parsed.store, prefixes: parsed.prefixes, text: answer.turtle }
        })
      })
    },

    // The endpoint is left: stop DuckDB (also when it is still starting) and the RDF generation,
    // and free the blob: addresses. (The downloaded data file itself is kept until the endpoint
    // is removed, see forgetDataFiles in dataSource.js.) A closed api is never used again: App.jsx
    // makes a new one when you come back, so from now on nothing starts any more (see closed).
    close() {
      closed = true
      stopper.abort()
      if (databasePromise !== null) {
        databasePromise.then((db) => db.close()).catch(() => null)
        databasePromise = null
      }
      if (fileUrl !== null) URL.revokeObjectURL(fileUrl)
      if (rdfUrl !== null) URL.revokeObjectURL(rdfUrl)
      fileUrl = null
      rdfUrl = null
    },
  }
}
