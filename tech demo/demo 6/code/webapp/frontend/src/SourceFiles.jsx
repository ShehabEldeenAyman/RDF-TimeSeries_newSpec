import { useState } from 'react'

// What the marks on the columns of the first rows mean
const BOUND_TEXT = {
  field: 'this field',
  time: 'time field',
  related: 'describes this field',
}

// The source files behind the series inspector, so nothing is hidden.
// The backend marks the lines (and columns) this field uses; we only colour them.
export function SourceFiles({ files }) {
  const [view, setView] = useState('turtle')

  return (
    <section className="card">
      <div className="source-tabs">
        <button className={view === 'turtle' ? 'source-tab active' : 'source-tab'} onClick={() => setView('turtle')}>
          Turtle
        </button>
        <button className={view === 'rows' ? 'source-tab active' : 'source-tab'} onClick={() => setView('rows')}>
          First {files.rows.rows.length > 0 ? files.rows.rows.length : ''} rows of the data
        </button>
      </div>

      {view === 'turtle' && (
        <>
          <p className="muted small long-text">
            <code>{files.turtleFile}</code> · marked: <span className="mark-field">this field</span>{' '}
            <span className="mark-time">the time field</span>{' '}
            <span className="mark-source">its source</span> ·{' '}
            <a href="#turtle-field">jump to this field</a>
          </p>
          <MarkedLines lines={files.turtle} />
        </>
      )}

      {view === 'rows' && <DataRows rows={files.rows} />}
    </section>
  )
}

// A text file, line by line. A line with a "mark" gets a coloured background.
function MarkedLines({ lines }) {
  return (
    <pre className="marked">
      {lines.map((line, index) => {
        // The first line of the field gets an id, so the "jump to this field" link can find it
        const firstFieldLine = line.mark === 'field' && (index === 0 || lines[index - 1].mark !== 'field')
        return (
          <span
            key={index}
            id={firstFieldLine ? 'turtle-field' : undefined}
            className={line.mark ? 'line mark-' + line.mark : 'line'}
          >
            {line.text === '' ? ' ' : line.text}
          </span>
        )
      })}
    </pre>
  )
}

// The first rows of the data as a table: a CSV or Parquet file, or the embedded points.
// The columns of this field, the time field and the fields that describe this one are coloured.
function DataRows({ rows }) {
  if (rows.error) return <p className="error long-text">{rows.error}</p>

  return (
    <>
      <p className="muted small long-text">
        <code>{rows.file}</code> · marked: <span className="mark-field">this field</span>{' '}
        <span className="mark-time">the time field</span>{' '}
        <span className="mark-related">fields that describe this one</span>
      </p>
      {rows.notFound.length > 0 && (
        <p className="error long-text">Not found in the data: {rows.notFound.join(', ')}</p>
      )}
      <div className="table-scroll">
        <table className="data">
          <thead>
            <tr>
              {rows.columns.map((column, index) => (
                <th key={index} className={column.bound ? 'mark-' + column.bound : ''}>
                  {column.header}
                  {column.bound && <div className="small">{BOUND_TEXT[column.bound]}</div>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {row.map((cell, cellIndex) => {
                  const column = rows.columns[cellIndex]
                  return (
                    <td key={cellIndex} className={column && column.bound ? 'mark-' + column.bound : ''}>
                      {cell}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
