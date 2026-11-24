// The merge browser test (US-50): the built frontend in live mode against the backend's fake stack
// (backend/tests/worst_case/fake_stack.py: fake LLM providers, the search's stub stages, the database off). CI runs it
// on every push to main. Locally, from frontend/ (BACKEND_PYTHON: the backend's Python, if `python` isn't it):
//   MERGE_E2E=1 npx playwright test -c e2e/merge/playwright.merge.config.ts
import { defineConfig, devices } from "@playwright/test";

const FRONTEND = 3200;
const BACKEND = 8100;
const PYTHON = process.env["BACKEND_PYTHON"] ?? "python";

export default defineConfig({
  testDir: ".",
  timeout: 120_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"], ["html", { open: "never", outputFolder: "../../playwright-report/merge" }]],
  use: {
    baseURL: `http://localhost:${FRONTEND}`,
    reducedMotion: "reduce",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `${PYTHON} -m tests.worst_case.fake_stack --port ${BACKEND} --frontend-origin http://localhost:${FRONTEND}`,
      cwd: "../../../backend",
      url: `http://localhost:${BACKEND}/api/health`,
      timeout: 60_000,
      reuseExistingServer: false,
    },
    {
      command: "npm run build && npm start",
      cwd: "../..",
      url: `http://localhost:${FRONTEND}`,
      timeout: 240_000,
      reuseExistingServer: false,
      env: {
        PORT: String(FRONTEND),
        VITE_API_MODE: "live",
        VITE_API_BASE_URL: `http://localhost:${BACKEND}`,
      },
    },
  ],
});
