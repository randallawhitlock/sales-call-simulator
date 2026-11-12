import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Tests read the stylesheet to pin layout rules jsdom cannot show.
  test: { css: true },
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
