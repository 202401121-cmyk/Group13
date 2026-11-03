// End-to-end test of the whole flow in mock mode, against the built app run with plain node (npm run test:e2e).
// Needs a browser once: npx playwright install chromium
import { defineConfig, devices } from "@playwright/test";

const PORT = 3100; // not 3000, so it never clashes with `npm run dev`

export default defineConfig({
  testDir: "e2e",
  timeout: 120_000,
  fullyParallel: false,
  reporter: [["list"]],
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
