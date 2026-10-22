import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

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
    // Requests to /api/... (data and computed answers) and /files/... (the RDF documents:
    // metadata endpoints and SHACL shapes) are passed on to the FastAPI backend.
    // For the browser everything comes from localhost:5174, so CORS is not needed.
    // changeOrigin false: the backend sees "Host: localhost:5174", so the IRIs it writes
    // in the catalogue (http://localhost:5174/files/...) are the ones the browser uses.
    proxy: {
      '/api': { target: 'http://127.0.0.1:8001', changeOrigin: false },
      '/files': { target: 'http://127.0.0.1:8001', changeOrigin: false },
    },
  },
})
