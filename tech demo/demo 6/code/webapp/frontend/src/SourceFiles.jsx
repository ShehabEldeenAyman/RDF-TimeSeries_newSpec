import { useState } from 'react'

// The source files behind the series inspector, so nothing is hidden.
// The backend marks the lines (and columns) this series uses; we only colour them.
export function SourceFiles({ files }) {
  const [view, setView] = useState('turtle')

  return (
    <section className="card">
      <div className="source-tabs">
        <button className={view === 'turtle' ? 'source-tab active' : 'source-tab'} onClick={() => setView('turtle')}>
          Turtle
        </button>
        {files.embedded ? <button className={view === 'points' ? 'source-tab active' : 'source-tab'} onClick={() => setView('points')}>Points JSON</button> : <>
        <button className={view === 'csvw' ? 'source-tab active' : 'source-tab'} onClick={() => setView('csvw')}>
          CSVW JSON
        </button>
        <button className={view === 'csv' ? 'source-tab active' : 'source-tab'} onClick={() => setView('csv')}>
          First 10 CSV rows
        </button>
        </>}
      </div>

      {view === 'turtle' && (
        <>
          <p className="muted small long-text">
            <code>{files.turtleFile}</code> · marked: <span className="mark-snippet">this snippet</span>{' '}
            <span className="mark-group">its group</span> ·{' '}
            <a href="#turtle-snippet">jump to this snippet</a>
          </p>
          <MarkedLines lines={files.turtle} />
        </>
      )}

      {view === 'csvw' && files.csvw === null && (
        <p className="error long-text">
          File not found: <code>{files.csvwFile}</code>
        </p>
      )}
      {view === 'csvw' && files.csvw !== null && (
        <>
          <p className="muted small long-text">
            <code>{files.csvwFile}</code> · marked: <span className="mark-time">the time column</span>{' '}
            <span className="mark-value">the value column</span>
          </p>
          <MarkedLines lines={files.csvw} />
        </>
      )}

      {view === 'csv' && <CsvRows csv={files.csv} />}
      {view === 'points' && <pre>{files.points}</pre>}
    </section>
  )
}

// A text file, line by line. A line with a "mark" gets a coloured background.
function MarkedLines({ lines }) {
  return (
    <pre className="marked">
      {lines.map((line, index) => {
        // The first line of the snippet gets an id, so the "jump to this snippet" link can find it
        const firstSnippetLine = line.mark === 'snippet' && (index === 0 || lines[index - 1].mark !== 'snippet')
        return (
          <span
            key={index}
            id={firstSnippetLine ? 'turtle-snippet' : undefined}
            className={line.mark ? 'line mark-' + line.mark : 'line'}
          >
            {line.text === '' ? ' ' : line.text}
          </span>
        )
      })}
    </pre>
  )
}

// The first CSV rows as a table. The columns this series reads are coloured.
function CsvRows({ csv }) {
  if (csv.error) return <p className="error long-text">{csv.error}</p>

  return (
    <>
      <p className="muted small long-text">
        <code>{csv.file}</code> · marked: the columns this series reads
      </p>
      <div className="table-scroll">
        <table className="data">
          <thead>
            <tr>
              {csv.columns.map((column) => (
                <th key={column.header} className={column.bound ? 'bound' : ''}>
                  {column.header}
                  {column.bound && <div className="small">{column.bound}</div>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {csv.rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {row.map((cell, cellIndex) => (
                  <td key={cellIndex} className={csv.columns[cellIndex].bound ? 'bound' : ''}>
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
