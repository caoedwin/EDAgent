import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 本地开发：npm run dev 后 /api 自动代理到本地 18080 端口的 agent-api
// 生产：vite build 产物由 FastAPI 静态托管（同源，无需代理）
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:18080',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 1500,
  },
})
