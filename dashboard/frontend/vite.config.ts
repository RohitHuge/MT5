import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const DASHBOARD_URL = process.env.DASHBOARD_DEV_URL ?? 'http://localhost:3000'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': DASHBOARD_URL,
      '/ws': { target: DASHBOARD_URL, ws: true },
    },
  },
})
