import { one, isEmbedded, TSS_DATA_FILE, TSS_FORMAT, TSS_PARQUET } from './tss'
import { FileViewer } from './FileViewer'
import { PointsViewer } from './PointsViewer'

// The Files tab: the files behind the picked metadata endpoint, so nothing is hidden.
//   external source (tss:embedded false): its data file (tss:dataFile), previewed and downloadable (FileViewer)
//   embedded source (tss:embedded true):  the points inside the metadata (tss:points) (PointsViewer)
//   both:                                 the metadata document itself
//   endpoint: the endpoint ({ id, iri, ... }); rdf: the parsed document ({ store, text, source, points, ... })
//   api:      the endpoint's data questions (dataApi.js)
export function Files({ endpoint, rdf, api }) {
  const embedded = isEmbedded(rdf.store, rdf.source) // true, false, or null (not stated)
  const format = one(rdf.store, rdf.source, TSS_FORMAT)
  // Compare the full IRI: the document may use any prefix for tss:, or none at all
  const parquet = format !== null && format.value === TSS_PARQUET

  // The tss:dataFile IRI, already resolved against the document's IRI by the parser, e.g.
  // http://localhost:5174/files/data/waterinfo_289423042.csv. We only show it as text: the
  // backend does not serve data/ to the browser, so the preview and the download of a bundled
  // endpoint go through its /data routes instead (see dataApi.js).
  const dataFile = one(rdf.store, rdf.source, TSS_DATA_FILE)

  return (
    <>
      {embedded === true ? (
        <PointsViewer rdf={rdf} />
      ) : (
        <FileViewer
          title={parquet ? 'Data file (tss:dataFile, Parquet previewed as CSV)' : 'Data file (tss:dataFile)'}
          api={api}
          location={dataFile ? dataFile.value : null}
          downloadLabel={parquet ? 'Download the original Parquet file' : 'Download the whole file'}
        />
      )}

      <section className="card">
        <h3>Metadata document</h3>
        <p className="muted small long-text">
          <DocumentText endpoint={endpoint} />, exactly as {endpoint.user ? 'your browser read it' : 'it was sent'}:
        </p>
        <pre>{rdf.text}</pre>
      </section>
    </>
  )
}

// Where the metadata document is, in words: its address, or the file you uploaded
function DocumentText({ endpoint }) {
  if (endpoint.user && endpoint.url === null) {
    return <>Uploaded from your computer: <strong>{endpoint.fileName}</strong> (read with the base IRI <code>{endpoint.iri}</code>)</>
  }
  return <a href={endpoint.iri} target="_blank" rel="noreferrer">{endpoint.iri}</a>
}
