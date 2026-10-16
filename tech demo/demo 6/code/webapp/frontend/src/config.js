// Where the FastAPI backend runs. Every component imports this,
// so if the backend moves you only change it here.
// Port 8001, because demo 2's backend uses 8000.
export const API_URL = '/api'

// FastAPI puts its error message in "detail". Usually that is a text, but for a
// wrongly typed parameter it is a list of objects, which React can't show.
// This turns either one into text.
export function errorText(detail) {
  if (typeof detail === 'string') return detail
  return JSON.stringify(detail)
}

// When fetch itself fails, the browser only says "Failed to fetch".
// This adds where we tried to go, and what to check.
export function fetchErrorText(err) {
  return `${err.message}: could not reach the backend at ${API_URL}. Is it still running? Check its terminal.`
}

