/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// In development the API is proxied same-origin so the httpOnly refresh cookie works.
const apiTarget = process.env.VITE_API_PROXY ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    // three.js + react-three are only needed by the (lazy) workspace; a separate long-cached
    // vendor chunk survives app updates. Its size (~1 MB, ~270 kB gzip) is expected.
    chunkSizeWarningLimit: 1100,
    rollupOptions: {
      output: {
        manualChunks(id) {
          // Vite's lazy-import helper and Rollup's CJS helpers are needed by the entry itself
          if (id.includes('vite/preload-helper') || id.includes('commonjsHelpers')) return 'vendor'
          if (/node_modules[\/](three|three-stdlib|@react-three)[\/]/.test(id)) return 'vendor-3d'
          // shared by every page: keep it out of vendor-3d so the dashboard never loads three.js
          if (/node_modules[\/](react|react-dom|scheduler|react-router|react-router-dom|@tanstack|zustand)[\/]/.test(id)) return 'vendor'
        },
      },
    },
  },
  server: {
    proxy: {
      '/api': { target: apiTarget, changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    restoreMocks: true,
  },
})
