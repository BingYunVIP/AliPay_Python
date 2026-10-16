import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    allowedHosts: (process.env.BYPAY_VITE_HOSTS || '').split(',').filter(Boolean),
    proxy: Object.fromEntries(['/api', '/alipay', '/health'].map(path => [path, process.env.BYPAY_BACKEND_ORIGIN || 'http://127.0.0.1:889'])),
  },
  build: { sourcemap: false },
})
