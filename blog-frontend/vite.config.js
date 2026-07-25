import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev: proxy /api to the local backend (ask app on :8080) so the browser talks
// to one origin — mirrors how CloudFront fronts /api in production.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
})
