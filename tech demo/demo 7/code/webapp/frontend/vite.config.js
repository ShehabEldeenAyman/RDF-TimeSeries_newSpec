import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'

// The full path of a file or folder, from its place relative to this config file
function pathHere(relativePath) {
  return fileURLToPath(new URL(relativePath, import.meta.url))
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Demo 7 runs on port 5174, with its backend on 8001. Demo 6 uses the same two
  // ports, so only one of the two demos can run at a time.
  // strictPort: stop with an error if 5174 is busy, instead of quietly picking another port
  // (the backend only accepts requests from pages on port 5174)
  server: {
    port: 5174,
    strictPort: true,
    // Never send what the page writes to its console (errors, warnings) to this dev server.
    // Vite does that by default when a coding agent (such as Claude Code) starts it, and prints
    // it in the terminal: for your own endpoints that could be your metadata or your data, which
    // must stay in the browser tab.
    forwardConsole: false,
    // Requests to /api/... (data and computed answers) and /files/... (the RDF documents:
    // metadata endpoints and SHACL shapes) are passed on to the FastAPI backend.
    // For the browser everything comes from localhost:5174, so CORS is not needed.
    // changeOrigin false: the backend sees "Host: localhost:5174", so the IRIs it writes
    // in the catalogue (http://localhost:5174/files/...) are the ones the browser uses.
    proxy: {
      '/api': { target: 'http://127.0.0.1:8001', changeOrigin: false },
      '/files': { target: 'http://127.0.0.1:8001', changeOrigin: false },
    },
    // The dev server only serves files from these places. Besides this frontend folder, the
    // browser's SHACL check (src/browser/shaclWorker.js) imports two of the server's own Python
    // files and the two default shapes files with "?raw", and they are outside it (in code/ and
    // SHACL/). Only those four files are added.
    fs: {
      allow: [
        pathHere('.'),
        pathHere('../../shacl_report/shacl_report.py'),
        pathHere('../../TSS2RDF/tss2rdf.py'),
        pathHere('../../../SHACL/external_shapes.ttl'),
        pathHere('../../../SHACL/embedded_shapes.ttl'),
      ],
    },
  },
})
