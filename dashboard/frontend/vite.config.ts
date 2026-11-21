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
  build: {
    rollupOptions: {
      output: {
        // Vite 8 bundles with Rolldown, which does not support Rollup's object
        // form of manualChunks — codeSplitting.groups is the native equivalent.
        codeSplitting: {
          groups: [
            {
              name: 'vendor-react',
              test: /[\\/]node_modules[\\/](react|react-dom|react-router|react-router-dom)[\\/]/,
            },
            {
              name: 'vendor-query',
              test: /[\\/]node_modules[\\/]@tanstack[\\/]/,
            },
            {
              name: 'vendor-charts',
              test: /[\\/]node_modules[\\/](lightweight-charts|recharts)[\\/]/,
            },
            {
              name: 'vendor-ui',
              test: /[\\/]node_modules[\\/](@radix-ui|lucide-react|zustand)[\\/]/,
            },
          ],
        },
      },
    },
  },
})
