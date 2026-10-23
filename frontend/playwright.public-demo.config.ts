import { defineConfig, devices } from "@playwright/test";
import path from "node:path";
const python = process.env.HFT_E2E_PYTHON ?? path.resolve("..", ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
export default defineConfig({
  testDir: "./e2e", testMatch: "public-demo.public.spec.ts", workers: 1, timeout: 60_000,
  use: { baseURL: "http://127.0.0.1:4176", trace: "retain-on-failure" },
  webServer: [
    { command: `"${python}" e2e/api_server.py`, url: "http://127.0.0.1:8009/api/health", timeout: 60_000, env: { HFT_E2E_PUBLIC_DEMO: "true", HFT_E2E_API_PORT: "8009" } },
    { command: "pnpm exec vite --host 127.0.0.1 --port 4176 --strictPort", url: "http://127.0.0.1:4176", env: { HFT_API_PROXY_TARGET: "http://127.0.0.1:8009", VITE_PUBLIC_DEMO: "true" } },
  ],
  projects: [
    { name: "desktop-public", use: { ...devices["Desktop Chrome"], channel: "chrome" } },
    { name: "mobile-public", use: { ...devices["iPhone 13"], browserName: "chromium", channel: "chrome" } },
  ],
});
