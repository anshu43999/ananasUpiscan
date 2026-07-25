import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

const backendTarget = process.env.UPISCAN_BACKEND_URL || 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api/publisher': {
        target: backendTarget,
        changeOrigin: true,
      },
      '/api/extract': {
        target: backendTarget,
        changeOrigin: true,
        ws: true,
      },
      '/api/config': {
        target: backendTarget,
        changeOrigin: true,
      },
      '/api/settings': {
        target: backendTarget,
        changeOrigin: true,
      },
      '/api/proxy-chain-test': {
        target: backendTarget,
        changeOrigin: true,
      },
    },
  },
});
