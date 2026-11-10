import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The chat API (agents-server) runs on 127.0.0.1:8000; the dev server proxies /api to it.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true },
  server: { host: "127.0.0.1", proxy: { "/api": "http://127.0.0.1:8000" } },
});
