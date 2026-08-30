import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

// 浏览器测试运行时放在项目所在的 E 盘，避免占用用户已紧张的系统盘空间。
process.env.PLAYWRIGHT_BROWSERS_PATH ??= path.resolve(".playwright-browsers");

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  use: {
    baseURL: "http://127.0.0.1:4173",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "node ./node_modules/vite/bin/vite.js --host 127.0.0.1 --port 4173",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: false,
  },
  projects: [
    { name: "desktop-chromium", use: { ...devices["Desktop Chrome"], channel: "chrome" } },
    {
      name: "mobile-375",
      use: { ...devices["iPhone 13"], browserName: "chromium", channel: "chrome" },
    },
  ],
});
