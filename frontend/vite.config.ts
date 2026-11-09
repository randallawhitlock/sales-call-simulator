import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        // SIM_API_URL lets a second checkout run its own backend on another port.
        target: process.env['SIM_API_URL'] ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
