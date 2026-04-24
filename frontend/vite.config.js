import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/generate-level': 'http://localhost:8000',
      '/serve-assets': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
})
