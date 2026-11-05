// csvFile.js: reading a data file (a File or Blob) the way the backend's Python does: line by line,
// 1 MB at a time, with the line endings Python sees in a file opened with newline=""
// (eachLine, like files.read_lines), and the cells of each CSV record the way Python's csv module
// reads them (CsvReader, a port of CPython's Modules/_csv.c).
//
// It is a port of code/webapp/backend/files.py (read_lines) and of Python's own csv.reader.
// Used by dataReading.js (the rows of a CSV file), query.js (GET /data shows the first lines as
// they are), duck.js (the header line of the SQL table) and observationsWorker.js (the converter).
// Reading a file gives Promises: eachLine is an async function (see the note at the top of query.js).

const CHUNK_SIZE = 1 << 20 // read a file 1 MB at a time

// Go through the lines of a text file (a Blob), the way Python reads a file opened with
// newline="": a line ends at \n, \r\n or \r. visit(text, ending) gets each line without its
// ending, and the ending itself ('' for a last line without one); when it returns false, the
// reading stops. encoding: a label TextDecoder knows ('utf-8', ...). A UTF-8 byte order mark
// stays in the text, as in Python. Gives a Promise that is done when the reading is done.
export async function eachLine(blob, visit, encoding = 'utf-8') {
  let decoder
  try {
    decoder = new TextDecoder(encoding, { fatal: true, ignoreBOM: true })
  } catch {
    throw new Error(`unknown encoding: ${encoding}`)
  }
  let rest = '' // the start of a line whose end is in the next part of the file
  let stopped = false

  // Hand out every complete line in text; keep the incomplete end in rest
  function handOut(text, last) {
    let start = 0
    for (let i = 0; i < text.length; i++) {
      const character = text[i]
      if (character !== '\n' && character !== '\r') continue
      let ending = character
      if (character === '\r') {
        if (i + 1 === text.length && !last) break // a \r at the end: maybe \r\n, wait for more
        if (text[i + 1] === '\n') ending = '\r\n'
      }
      if (visit(text.slice(start, i), ending) === false) {
        stopped = true
        return ''
      }
      i += ending.length - 1
      start = i + 1
    }
    return text.slice(start)
  }

  for (let offset = 0; offset < blob.size && !stopped; offset += CHUNK_SIZE) {
    const bytes = new Uint8Array(await blob.slice(offset, offset + CHUNK_SIZE).arrayBuffer())
    let text
    try {
      text = decoder.decode(bytes, { stream: true })
    } catch {
      throw new Error(`'${encoding}' codec can't decode the file: it is not valid ${encoding} text`)
    }
    rest = handOut(rest + text, false)
  }
  if (stopped) return
  let end
  try {
    end = decoder.decode()
  } catch {
    throw new Error(`'${encoding}' codec can't decode the file: it is not valid ${encoding} text`)
  }
  rest = handOut(rest + end, true)
  if (!stopped && rest !== '') visit(rest, '')
}

// Python's csv module (Modules/_csv.c) with its default dialect: quote character ",
// a quote inside a quoted cell written twice, no escape character, strict off.
// Feed it one line at a time (feed); it gives a record (an array of cells) when a line completes one.
// It is the app's only class: one object (new CsvReader(',')) that keeps where it is in a record
// (this.state, this.field, ...) from one line to the next, like the C code's reader object.
const START_RECORD = 0
const START_FIELD = 1
const IN_FIELD = 2
const IN_QUOTED_FIELD = 3
const QUOTE_IN_QUOTED_FIELD = 4
const EAT_CRNL = 5
const FIELD_LIMIT = 131072 // csv.field_size_limit()

export class CsvReader {
  constructor(delimiter) {
    this.delimiter = delimiter
    this.lineNumber = 0 // how many lines were read (csv's reader.line_num)
    this.fields = []
    this.field = ''
    this.state = START_RECORD
  }

  saveField() {
    this.fields.push(this.field)
    this.field = ''
  }

  addCharacter(character) {
    if (this.field.length >= FIELD_LIMIT) throw new Error(`field larger than field limit (${FIELD_LIMIT})`)
    this.field += character
  }

  // One character; null is the end of a line (EOL in _csv.c)
  process(c) {
    const newline = c === '\n' || c === '\r'
    switch (this.state) {
      case START_RECORD:
        if (c === null) return // an empty line: an empty record
        if (newline) {
          this.state = EAT_CRNL
          return
        }
        this.state = START_FIELD
      // falls through: a normal character starts the first cell
      case START_FIELD:
        if (newline || c === null) {
          this.saveField()
          this.state = c === null ? START_RECORD : EAT_CRNL
        } else if (c === '"') {
          this.state = IN_QUOTED_FIELD
        } else if (c === this.delimiter) {
          this.saveField()
        } else {
          this.addCharacter(c)
          this.state = IN_FIELD
        }
        return
      case IN_FIELD:
        if (newline || c === null) {
          this.saveField()
          this.state = c === null ? START_RECORD : EAT_CRNL
        } else if (c === this.delimiter) {
          this.saveField()
          this.state = START_FIELD
        } else {
          this.addCharacter(c)
        }
        return
      case IN_QUOTED_FIELD:
        if (c === null) return // the cell goes on on the next line
        if (c === '"') this.state = QUOTE_IN_QUOTED_FIELD
        else this.addCharacter(c)
        return
      case QUOTE_IN_QUOTED_FIELD:
        if (c === '"') {
          this.addCharacter(c) // "" inside quotes is one "
          this.state = IN_QUOTED_FIELD
        } else if (c === this.delimiter) {
          this.saveField()
          this.state = START_FIELD
        } else if (newline || c === null) {
          this.saveField()
          this.state = c === null ? START_RECORD : EAT_CRNL
        } else {
          this.addCharacter(c) // strict off: a character after the closing quote is kept
          this.state = IN_FIELD
        }
        return
      case EAT_CRNL:
        if (newline) return
        if (c === null) {
          this.state = START_RECORD
          return
        }
        throw new Error("new-line character seen in unquoted field - do you need to open the file with newline=''?")
    }
  }

  // One line: its text, with its line ending or without it. Gives the record it completes, or
  // null when a quoted cell goes on on the next line.
  feed(text) {
    this.lineNumber += 1
    // A line without quotes that starts a record: the cells are the parts between the delimiters
    if (this.state === START_RECORD && !text.includes('"') && this.delimiter.length === 1 &&
        !/[\r\n]./.test(text)) {
      const content = text.replace(/[\r\n]+$/, '')
      if (content === '') return []
      const cells = content.split(this.delimiter)
      for (const cell of cells) {
        if (cell.length > FIELD_LIMIT) throw new Error(`field larger than field limit (${FIELD_LIMIT})`)
      }
      return cells
    }
    for (const character of text) this.process(character)
    this.process(null)
    if (this.state !== START_RECORD) return null
    const record = this.fields
    this.fields = []
    return record
  }

  // The end of the file: a last record that was not finished (a quoted cell without its end), or null
  finish() {
    if (this.field.length !== 0 || this.state === IN_QUOTED_FIELD) {
      this.saveField()
      const record = this.fields
      this.fields = []
      return record
    }
    return null
  }
}

// Go through the records of a CSV file (a Blob), the way data_reading.py does it: csv.reader
// over the lines of the file without their line endings (files.read_lines). visit(cells) gets
// each record; returning false stops the reading.
export async function eachCsvRecord(blob, delimiter, visit) {
  const reader = new CsvReader(delimiter)
  let stopped = false
  await eachLine(blob, (text) => {
    const record = reader.feed(text)
    if (record !== null && visit(record) === false) {
      stopped = true
      return false
    }
    return true
  })
  if (!stopped) {
    const last = reader.finish()
    if (last !== null) visit(last)
  }
}

// The first line of a CSV file as cells (next(csv.reader(...), [])): the header line
export async function firstCsvRecord(blob, delimiter) {
  let first = []
  await eachCsvRecord(blob, delimiter, (cells) => {
    first = cells
    return false
  })
  return first
}
