import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Demo 2 uses port 5173, so demo 3 gets its own port.
  // strictPort: stop with an error if 5174 is busy, instead of quietly picking another port
  // (the backend only accepts requests from pages on port 5174)
    server: {
    port: 5174,
    strictPort: true,
    // Requests to /api/... are passed on to the FastAPI backend.
    // For the browser everything comes from localhost:5174, so CORS is not needed.
    proxy: {
      '/api': 'http://127.0.0.1:8001',
    },
  },
})

