import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: { proxy: { '/api': 'http://127.0.0.1:8000', '/alipay': 'http://127.0.0.1:8000' } },
  build: { sourcemap: false },
})
