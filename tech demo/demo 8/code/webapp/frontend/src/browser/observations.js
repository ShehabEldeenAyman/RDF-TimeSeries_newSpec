// observations.js: the RDF generation tab for an endpoint whose data must stay in this tab.
// It turns the source's data into SOSA observations, exactly like the backend's POST /rdf
// (code/TSS2RDF/tss2rdf.py generate), but in a Web Worker in the browser (observationsWorker.js):
// the data never leaves the tab.
//
// Usage:
//   import { generateObservations } from './browser/observations.js'
//
//   generateObservations({
//     metadataText: rdf.text,          // the metadata document, as text
//     contentType: 'text/turtle',      // its syntax
//     baseIRI: endpoint.iri,           // the document's IRI (relative IRIs in it are read against it)
//     dataFile: file,                  // the tss:dataFile contents (File/Blob), or null for embedded
//                                      // points (the worker reads those from metadataText itself)
//     getDatabase: getDatabase,        // optional, for a Parquet file: a function giving a Promise of
//                                      // the endpoint's db (duck.js). Without it DuckDB is started
//                                      // just to read the file, and stopped again.
//     signal: controller.signal,       // optional: controller.abort() stops the worker (when the
//                                      // endpoint is left), and the Promise is rejected
//   })
//     .then((result) => {
//       result.observations, result.triples      // how many
//       result.series                             // [{ field: <field IRI>, observations: n }]
//       result.previewTurtle                      // the first 200 observations, as Turtle (parse it with parseRdf)
//       result.previewObservations, result.previewTruncated
//       result.ntriples                           // every triple, as an N-Triples Blob (to download)
//       result.bytes, result.filename             // its size, and 'observations.nt'
//     })
//     .catch((error) => setError(error.message))  // the converter's message, like the backend's "detail"

// The file name the download gets (rdf_routes.GENERATED_RDF_FILENAME)
export const GENERATED_RDF_FILENAME = 'observations.nt'

// The rows of the Parquet file, each value as the converter writes it (tss2rdf.cell_text)
function parquetRows(dataFile, getDatabase) {
  if (getDatabase) {
    return getDatabase().then((db) => db.parquetCells('rdf', null))
  }
  // No database for this endpoint yet: start DuckDB only to read the file (duck.js is big, so
  // it is loaded only now)
  return import('./duck.js').then((duck) => duck.readParquetFile(dataFile, 'rdf'))
}

export function generateObservations({ metadataText, contentType, baseIRI, dataFile, getDatabase, signal }) {
  return new Promise((resolve, reject) => {
    if (signal && signal.aborted) {
      reject(new Error('The RDF generation was stopped.'))
      return
    }
    // A module worker: it imports n3, rdf.js, tss.js and the engine's helpers (python.js,
    // values.js, sourceReading.js, csvFile.js) like the page does
    const worker = new Worker(new URL('./observationsWorker.js', import.meta.url), { type: 'module' })

    // Stop the worker (its memory is freed now) and stop listening for an abort
    function finish() {
      worker.terminate()
      if (signal) signal.removeEventListener('abort', stop)
    }
    // signal.abort(): the endpoint was left, nobody waits for the observations any more
    function stop() {
      finish()
      reject(new Error('The RDF generation was stopped.'))
    }
    if (signal) signal.addEventListener('abort', stop)

    worker.onmessage = (event) => {
      const message = event.data
      if (message.type === 'parquet') {
        // The worker needs the rows of the Parquet file: DuckDB reads them on the page
        parquetRows(dataFile, getDatabase)
          .then((table) => worker.postMessage({ type: 'parquet', names: table.names, rows: table.rows }))
          .catch((error) => worker.postMessage({ type: 'parquet', error: error.message }))
      } else if (message.type === 'done') {
        finish() // a new worker for every run: its memory is freed now
        const result = message.result
        result.filename = GENERATED_RDF_FILENAME
        resolve(result)
      } else if (message.type === 'error') {
        finish()
        reject(new Error(message.message))
      }
    }
    // The worker itself failed (it could not load, or ran out of memory)
    worker.onerror = (event) => {
      finish()
      reject(new Error(`The RDF generation stopped: ${event.message || 'the worker failed'}`))
    }

    worker.postMessage({
      type: 'start',
      metadataText: metadataText,
      contentType: contentType || 'text/turtle',
      baseIRI: baseIRI,
      dataFile: dataFile || null,
    })
  })
}
