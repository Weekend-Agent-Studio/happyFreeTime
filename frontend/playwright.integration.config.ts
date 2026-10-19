import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

const python = process.env.HFT_E2E_PYTHON ?? path.resolve(
  "..", ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
);

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.integration.spec.ts",
  timeout: 60_000,
  workers: 1,
  use: { baseURL: "http://127.0.0.1:4174", trace: "retain-on-failure" },
  webServer: [
    {
      command: `"${python}" e2e/api_server.py`,
      url: "http://127.0.0.1:8007/api/health",
      timeout: 60_000,
      reuseExistingServer: false,
    },
    {
      command: "pnpm exec vite --host 127.0.0.1 --port 4174 --strictPort",
      url: "http://127.0.0.1:4174",
      env: { HFT_API_PROXY_TARGET: "http://127.0.0.1:8007" },
      reuseExistingServer: false,
    },
  ],
  projects: [
    { name: "desktop-api", use: { ...devices["Desktop Chrome"], channel: "chrome" } },
    { name: "mobile-api", use: { ...devices["iPhone 13"], browserName: "chromium", channel: "chrome" } },
  ],
});
