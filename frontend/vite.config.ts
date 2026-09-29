import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  base: './',
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 5173,
    host: '0.0.0.0',
    proxy: {
      '/api': {
        target: `http://localhost:${process.env.TINGKEBANLV_PORT || '8765'}`,
        changeOrigin: true,
      },
      '/ws': {
        target: `ws://localhost:${process.env.TINGKEBANLV_PORT || '8765'}`,
        ws: true,
      },
    },
  },
})
