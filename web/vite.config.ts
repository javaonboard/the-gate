import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Everything under /api goes to the FastAPI backend, including the SSE
      // stream. buffer:false keeps events flowing instead of being batched.
      // Cropped faces are served by the backend, not by Vite.
      "/faces": { target: "http://127.0.0.1:8080", changeOrigin: true },
      "/api": {
        target: "http://127.0.0.1:8080",
        changeOrigin: true,
        ws: false,
        configure: (proxy) => {
          proxy.on("proxyRes", (proxyRes) => {
            if (proxyRes.headers["content-type"]?.includes("event-stream")) {
              proxyRes.headers["cache-control"] = "no-cache";
            }
          });
        },
      },
    },
  },
});
