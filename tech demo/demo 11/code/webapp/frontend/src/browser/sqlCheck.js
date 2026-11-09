// sqlCheck.js: is a SQL text exactly one reading query? The SQL box runs nothing else, and says
// why with the backend's messages: "Write exactly one query (found 2).", "Only reading queries
// (SELECT) are allowed, not DROP.", or DuckDB's own syntax error. DuckDB's own parser and
// tokenizer decide, in its Web Worker.
//
// It ports the check at the start of sql_box.run_query (code/webapp/backend/sql_box.py, where
// Python's duckdb names the statement types). Used by duck.js (db.run). Asking DuckDB gives
// Promises: async functions (see the note at the top of duck.js).

import { sqlText } from './sqlBox.js'

// The statement type Python's duckdb names, by the first word of a statement that is no SELECT
const STATEMENT_TYPES = {
  INSERT: 'INSERT', UPDATE: 'UPDATE', DELETE: 'DELETE', TRUNCATE: 'DELETE',
  CREATE: 'CREATE', DROP: 'DROP', DEALLOCATE: 'DROP', ALTER: 'ALTER',
  BEGIN: 'TRANSACTION', START: 'TRANSACTION', COMMIT: 'TRANSACTION', END: 'TRANSACTION',
  ROLLBACK: 'TRANSACTION', ABORT: 'TRANSACTION',
  SET: 'SET', RESET: 'SET', USE: 'SET',
  COPY: 'COPY', EXPORT: 'EXPORT', IMPORT: 'PRAGMA', EXPLAIN: 'EXPLAIN', VACUUM: 'VACUUM', ANALYZE: 'VACUUM',
  CALL: 'CALL', CHECKPOINT: 'CALL', LOAD: 'LOAD', INSTALL: 'LOAD', ATTACH: 'ATTACH', DETACH: 'DETACH',
  PREPARE: 'PREPARE', EXECUTE: 'EXECUTE', MERGE: 'MERGE_INTO', PRAGMA: 'PRAGMA',
}
const TOKEN_OPERATOR = 3
const TOKEN_KEYWORD = 4

// The statements of a SQL text, as lists of DuckDB's tokens ({ text, type }): split at every ;
// that is no part of a text or a comment (DuckDB's own tokenizer says which ; are), leaving out
// empty statements, as DuckDB's parser does
async function splitStatements(database, sql) {
  const tokens = await database.tokenize(sql)
  const statements = []
  let current = []
  for (let i = 0; i < tokens.offsets.length; i++) {
    const end = i + 1 < tokens.offsets.length ? tokens.offsets[i + 1] : sql.length
    const text = sql.slice(tokens.offsets[i], end).trim()
    if (tokens.types[i] === TOKEN_OPERATOR && text.startsWith(';')) {
      if (current.length > 0) statements.push(current)
      current = []
    } else {
      current.push({ text: text, type: tokens.types[i] })
    }
  }
  if (current.length > 0) statements.push(current)
  return statements
}

// The name Python's duckdb gives the type of a statement that is no SELECT (e.g. INSERT)
function statementTypeName(tokens) {
  const words = tokens.filter((token) => token.type === TOKEN_KEYWORD).map((token) => token.text.toUpperCase())
  let first = words.length > 0 ? words[0] : (tokens.length > 0 ? tokens[0].text.toUpperCase() : '')
  if (first === 'WITH') {
    // WITH ... INSERT/UPDATE/DELETE: the statement after the common table expressions
    for (const word of words) {
      if (['INSERT', 'UPDATE', 'DELETE', 'MERGE'].includes(word)) {
        first = word
        break
      }
    }
  }
  if (first === 'FORCE' && words.length > 1) first = words[1] === 'CHECKPOINT' ? 'CHECKPOINT' : 'INSTALL'
  return Object.hasOwn(STATEMENT_TYPES, first) ? STATEMENT_TYPES[first] : first
}

// Stop with the backend's message when the text is not exactly one reading query (SELECT, WITH
// ... SELECT, FROM ..., DESCRIBE, SUMMARIZE, SHOW). DuckDB's json_serialize_sql parses the text
// with DuckDB's own parser and only accepts SELECT statements: it says how many there are, or
// that one is no SELECT, or that the text has a syntax error. The database stays in this tab
// for many queries (the backend makes a new one for each), so a query must never change it.
export async function checkStatements(database, connection, sql) {
  const answer = await connection.query(`SELECT json_serialize_sql(${sqlText(sql)}) AS parsed`)
  const parsed = JSON.parse(answer.getChild('parsed').get(0))
  if (!parsed.error) {
    if (parsed.statements.length !== 1) throw new Error(`Write exactly one query (found ${parsed.statements.length}).`)
    return
  }
  if (parsed.error_type === 'parser') {
    // A syntax error: let DuckDB prepare the text (prepare never runs it) for its full message
    let statement
    try {
      statement = await connection.prepare(sql)
    } catch (error) {
      throw new Error(error.message, { cause: error })
    }
    await statement.close()
    throw new Error(`Parser Error: ${parsed.error_message}`)
  }
  const statements = await splitStatements(database, sql)
  const notSelect = parsed.error_message === 'Only SELECT statements can be serialized to json!'
  if (statements.length === 1 && notSelect && statementTypeName(statements[0]) === 'PIVOT') {
    // A PIVOT without IN (...) is two statements for DuckDB: one makes the list of values
    throw new Error('Write exactly one query (found 2).')
  }
  if (statements.length !== 1) throw new Error(`Write exactly one query (found ${statements.length}).`)
  if (notSelect) {
    throw new Error(`Only reading queries (SELECT) are allowed, not ${statementTypeName(statements[0])}.`)
  }
  // Another problem of json_serialize_sql (a query it can't write as JSON): accept it only
  // when it starts like a reading query
  const first = statements[0][0].text.toUpperCase()
  if (!['SELECT', 'WITH', 'FROM', 'VALUES', 'TABLE', 'DESCRIBE', 'SUMMARIZE', 'SHOW', 'UNPIVOT', '('].includes(first)) {
    throw new Error(`Only reading queries (SELECT) are allowed, not ${statementTypeName(statements[0])}.`)
  }
}
