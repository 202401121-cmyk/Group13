// The live end-to-end test: the real frontend, backend and LLM providers, started by scripts/dev.py.
// Never part of `npm run test:e2e` or CI. Run it from frontend/ while scripts/dev.py is running:
//   LIVE_E2E=1 npx playwright test -c e2e/live/playwright.live.config.ts
// (PowerShell: $env:LIVE_E2E=1; npx playwright test -c e2e/live/playwright.live.config.ts)
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  timeout: 300_000, // the search alone may take up to 170 s
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env["LIVE_BASE_URL"] ?? "http://localhost:3000",
    reducedMotion: "reduce",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
