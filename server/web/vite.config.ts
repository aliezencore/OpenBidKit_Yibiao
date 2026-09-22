/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// dev 形态：Vite 自持前端，API（/health /projects /tasks）转发给本机 uvicorn；
// 生产形态：方案 A 单体同进程，`npm run build` 产物由 FastAPI 静态挂载
// （app/main.py，dist 存在才挂）。R-P3d3-8。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/health': 'http://127.0.0.1:8000',
      '/projects': 'http://127.0.0.1:8000',
      '/tasks': 'http://127.0.0.1:8000',
    },
  },
  test: {
    environment: 'node',
  },
});
