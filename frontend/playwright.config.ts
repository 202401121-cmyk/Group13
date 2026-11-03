// End-to-end tests in mock mode, against the built app run with plain node (npm run test:e2e): the whole flow
// (e2e/flow.spec.ts) and the FE-4 quality specs (e2e/quality/). Needs a browser once: npx playwright install chromium
import { defineConfig, devices } from "@playwright/test";

const PORT = 3100; // not 3000, so it never clashes with `npm run dev`

export default defineConfig({
  testDir: "e2e",
  timeout: 120_000,
  fullyParallel: false,
  // The HTML report (playwright-report/, git-ignored) is what CI uploads when a test fails.
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: `http://localhost:${PORT}`,
    reducedMotion: "reduce", // typing effects finish at once, so every page is checked fully drawn
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npm run build && npm start",
    url: `http://localhost:${PORT}`,
    env: { PORT: String(PORT), VITE_API_MODE: "mock" },
    reuseExistingServer: false,
    timeout: 240_000,
  },
});
