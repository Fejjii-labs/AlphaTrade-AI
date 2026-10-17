import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

/** Dev/test only; no application route or backend connection. */
export default defineConfig({
  root: path.resolve(__dirname),
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "../src") } },
  css: { postcss: path.resolve(__dirname, "..") },
  server: { host: "127.0.0.1", port: 4175, strictPort: true },
});
