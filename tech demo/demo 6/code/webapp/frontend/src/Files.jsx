import { API_URL } from './config'
import { FileViewer } from './FileViewer'

// The Files tab: the files behind the picked data source.
// - external source: its data file (tss:dataFile), previewed and downloadable
// - embedded source: the points inside the Turtle file (tss:points)
// - both: the metadata Turtle file itself
export function Files({ info }) {
  const parquet = info.format === 'tss:Parquet'

  return (
    <>
      {info.embedded ? (
        <FileViewer
          key={`${info.id}-points`}
          title="Embedded points (tss:points)"
          url={`${API_URL}/endpoints/${info.id}/points`}
        />
      ) : (
        <FileViewer
          key={`${info.id}-data`}
          title={parquet ? 'Data file (tss:dataFile, Parquet previewed as CSV)' : 'Data file (tss:dataFile)'}
          url={`${API_URL}/endpoints/${info.id}/data`}
          downloadUrl={`${API_URL}/endpoints/${info.id}/data/download`}
          downloadLabel={parquet ? 'Download the original Parquet file' : 'Download the whole file'}
        />
      )}

      <section className="card">
        <h3>Metadata file (Turtle)</h3>
        <p className="muted small long-text">
          <code>{info.file}</code>
        </p>
        <pre>{info.turtle}</pre>
      </section>
    </>
  )
}
