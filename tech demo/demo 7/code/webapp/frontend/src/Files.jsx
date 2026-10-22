import { API_URL } from './config'
import { one, isEmbedded, TSS_DATA_FILE, TSS_FORMAT, TSS_PARQUET } from './tss'
import { FileViewer, PointsViewer } from './FileViewer'

// The Files tab: the files behind the picked metadata endpoint, so nothing is hidden.
//   external source (tss:embedded false): its data file (tss:dataFile), previewed and downloadable
//   embedded source (tss:embedded true):  the points inside the metadata (tss:points)
//   both:                                 the metadata document itself
//   endpoint: the catalogue entry ({ id, iri, ... }); rdf: the parsed document ({ store, text, source, ... })
export function Files({ endpoint, rdf }) {
  const embedded = isEmbedded(rdf.store, rdf.source) // true, false, or null (not stated)
  const format = one(rdf.store, rdf.source, TSS_FORMAT)
  // Compare the full IRI: the document may use any prefix for tss:, or none at all
  const parquet = format !== null && format.value === TSS_PARQUET

  // The tss:dataFile IRI, already resolved against the document's IRI by the parser, e.g.
  // http://localhost:5174/files/data/waterinfo_289423042.csv. We only show it as text:
  // the backend does not serve data/ to the browser, so the preview and the download go
  // through the /data routes of the endpoint instead.
  const dataFile = one(rdf.store, rdf.source, TSS_DATA_FILE)
  const dataUrl = `${API_URL}/endpoints/${encodeURIComponent(endpoint.id)}/data`

  return (
    <>
      {embedded === true ? (
        <PointsViewer rdf={rdf} />
      ) : (
        <FileViewer
          title={parquet ? 'Data file (tss:dataFile, Parquet previewed as CSV)' : 'Data file (tss:dataFile)'}
          url={dataUrl}
          location={dataFile ? dataFile.value : null}
          downloadUrl={dataUrl + '/download'}
          downloadLabel={parquet ? 'Download the original Parquet file' : 'Download the whole file'}
        />
      )}

      <section className="card">
        <h3>Metadata document</h3>
        <p className="muted small long-text">
          <a href={endpoint.iri} target="_blank" rel="noreferrer">{endpoint.iri}</a>, exactly as it was sent:
        </p>
        <pre>{rdf.text}</pre>
      </section>
    </>
  )
}
