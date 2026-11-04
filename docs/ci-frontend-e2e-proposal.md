# Proposal: run the frontend end-to-end and quality checks in CI

- **Status:** proposed, not in `.github/` yet. Palak Patel owns CI and decides whether, when and how to add it.
- **From:** FE-4, the frontend quality gate for Sprint 1 (Manu Pardhan)
- **Related:** [frontend-quality-report.md](frontend-quality-report.md), `frontend/playwright.config.ts`,
  `frontend/e2e/`

## Why

Today the `frontend` job in `.github/workflows/ci.yml` runs lint, the type check and the unit tests. Nothing in CI
opens the app in a browser. The flow test (`e2e/flow.spec.ts`) and the FE-4 quality specs (`e2e/quality/`) check
what unit tests can't: accessibility with axe on every screen and state, keyboard use, screen sizes, zoom,
forced colours, Web Vitals and the size of the JavaScript each page loads. They only run when someone remembers
to run them. A CI job would catch a broken screen before it is merged.

## What the job does

1. Sets up Node 20 (the same as the current `frontend` job; `engines.node` is `>=20.19`).
2. Runs `npm ci` (this also generates the contract types, as it does now).
3. Installs Chromium and its system libraries: `npx playwright install --with-deps chromium`.
4. Runs `npm run test:e2e`. The Playwright config already builds the app (`npm run build`), starts the built
   server (`npm start`) on port 3100 in mock mode (`VITE_API_MODE=mock`) and waits for it. So the job does not
   start the server itself: a separate build step would only build twice. Mock mode means no backend, no keys and
   no network calls to our APIs.
5. If anything fails, uploads the HTML report and the traces as an artifact for 14 days.

## Proposed job

Add this as a new job next to `frontend` in `.github/workflows/ci.yml`. It copies the `has-app` check the
`frontend` job already uses.

```yaml
frontend-e2e:
  runs-on: ubuntu-latest
  timeout-minutes: 30
  defaults:
    run:
      working-directory: frontend
  steps:
    - uses: actions/checkout@v4
    - id: has-app
      run: |
        if [ -f package.json ]; then echo "exists=true" >> "$GITHUB_OUTPUT"
        else echo "exists=false" >> "$GITHUB_OUTPUT"; fi
    - uses: actions/setup-node@v4
      if: steps.has-app.outputs.exists == 'true'
      with:
        node-version: "20"
        cache: npm
        cache-dependency-path: frontend/package-lock.json
    - run: npm ci
      if: steps.has-app.outputs.exists == 'true'
    - name: Install Chromium for Playwright
      if: steps.has-app.outputs.exists == 'true'
      run: npx playwright install --with-deps chromium
    - name: Build, start in mock mode, run the flow and quality specs
      if: steps.has-app.outputs.exists == 'true'
      run: npm run test:e2e
      env:
        VITE_API_MODE: mock
    - name: Upload the Playwright report
      if: failure() && steps.has-app.outputs.exists == 'true'
      uses: actions/upload-artifact@v4
      with:
        name: playwright-report
        path: |
          frontend/playwright-report/
          frontend/test-results/
        retention-days: 14
```

## Notes for the decision

- **HTML report.** `playwright.config.ts` now writes an HTML report to `frontend/playwright-report/` as well as
  the list output (FE-4 added it; it never opens a browser). Both folders are git-ignored.
- **Run time.** On a 16-thread Windows laptop, `npm run test:e2e` (build, then 37 tests with 8 workers) took
  181 seconds. GitHub's hosted Linux runners have fewer CPUs, so Playwright uses fewer workers there and the
  job will take longer. If it is too slow for every pull request, two options: run only `e2e/flow.spec.ts` and
  `e2e/quality/a11y-matrix.spec.ts` on pull requests and the full suite on `main`
  (`npx playwright test e2e/flow.spec.ts e2e/quality/a11y-matrix.spec.ts`), or add a `paths: [frontend/**]`
  filter so the job only runs when the frontend changes.
- **Flaky numbers.** The Web Vitals and frame-rate checks measure timing. Shared CI machines are slower and
  noisier than a laptop. If one of them fails without a code change, rerun it once before treating it as real,
  and tell Manu so the threshold can be reviewed. The accessibility, keyboard, size and zoom checks do not depend
  on speed.
- **Lighthouse** is not part of this job. It was run by hand for the quality report. Lighthouse CI could be added
  later as its own job; that is a separate decision.
- **Required check.** Whether `frontend-e2e` becomes a required check for merging is Palak's call. A sensible
  start is to run it on every pull request without making it required for one sprint, then decide.
- **Secrets.** None. The job runs in mock mode and needs no keys or tokens.
