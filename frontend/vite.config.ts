import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, the Vite server proxies API + WebSocket traffic to the FastAPI process on :8000.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
});
