import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 40000,
    proxy: {
      // 开发时把 /api 代理到后端，避免跨域
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    // 构建产物直接输出到后端 static 目录，Docker 中由 FastAPI 托管
    outDir: '../app/static',
    emptyOutDir: true,
  },
})
