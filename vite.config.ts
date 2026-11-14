import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 1420,
    strictPort: true,
    host: '127.0.0.1',
    // Native build outputs can be locked while Tauri is running on Windows.
    watch: { ignored: ['**/src-tauri/target/**', '**/artifacts/**', '**/build/**', '**/.venv/**'] },
  },
  clearScreen: false,
});
