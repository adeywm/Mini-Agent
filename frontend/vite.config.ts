import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The chat API (agents-server) runs on 127.0.0.1:8000; the dev server proxies /api to it.
// MINI_AGENT_API overrides the target, e.g. http://127.0.0.1:8001 when 8000 is taken.
const api = process.env.MINI_AGENT_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true },
  server: { host: "127.0.0.1", proxy: { "/api": api } },
});
