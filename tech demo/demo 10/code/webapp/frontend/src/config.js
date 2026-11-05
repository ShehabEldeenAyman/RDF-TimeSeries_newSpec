// Where the FastAPI backend's data and computed routes are. App.jsx and dataApi.js (which
// asks the backend for the tabs of a bundled endpoint) import this, so if the backend moves
// you only change it here. (Your own endpoints never use it: the browser answers for them.)
// It is a path on the page's own server: Vite (port 5174) passes /api/... and
// /files/... on to the backend on port 8001 (see vite.config.js).
// The RDF documents themselves (catalogue, metadata endpoints, SHACL shapes) are
// loaded and parsed with loadRdf from rdf.js.
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

