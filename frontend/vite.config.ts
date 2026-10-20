import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: false,
    exclude: ["e2e/**", "node_modules/**", "dist/**"],
  },
  server: {
    port: 5173,
    proxy: {
      "/api": process.env.HFT_API_PROXY_TARGET ?? "http://127.0.0.1:8000",
      "/_AMapService": process.env.HFT_API_PROXY_TARGET ?? "http://127.0.0.1:8000",
    },
  },
});
