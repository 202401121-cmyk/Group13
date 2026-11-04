# Sprint 1 verification

The final check of Sprint 1. Every part was tested on its own, then the whole system, against the 45 acceptance
criteria in `backlog/stories.yaml`, the Sprint 1 exit criteria and the team's decisions. Nothing in the application
was changed. Evidence files are in [sprint-1-verification/](sprint-1-verification/).

- **Commit tested:** `72d0433773f2e217ec96bd8d51f4466cbd71b6e8` (`main`, Merge pull request #37)
- **Date:** 4 November 2026, 17:27 to 18:50 UTC
- **Checked by:** Palak Patel (QA Engineer)
- **Machine:** Windows 11 (10.0.26300), Git Bash. Python 3.11.15 in a new virtual environment. Node 22.18.0 and
  npm 11.12.1. Docker 29.1.2. Playwright 1.64.1 with Chromium.
- **CI on that commit:** run 41871458849 passed all 5 jobs (backend, backend-windows, ml, secrets, frontend).

## 1. Summary

**Verdict: Not yet: 18 failures.** 22 checks failed and 2 are BLOCKED. Sprint 1 works end to end: the CLI and
the browser both run a request through every stage, with and without the database and with every LLM switched off.
But 18 faults remain in code, repository settings and process, listed below and in section 3.

| Part | PASS | FAIL | BLOCKED | N/A |
|---|---|---|---|---|
| S0 Set-up and inventory | 4 | 0 | 0 | 0 |
| A Static and automated | 7 | 0 | 1 | 0 |
| B LLM providers and the wrapper | 7 | 0 | 0 | 0 |
| C Database | 5 | 0 | 0 | 0 |
| D Backend, story by story | 4 | 3 | 0 | 1 |
| E Frontend (the sub-checks of E1, E2 and E5 to E7, plus E3, E4, E7.L and E8) | 43 | 16 | 0 | 4 |
| F Whole system | 4 | 1 | 0 | 0 |
| G Documentation and credit | 1 | 2 | 1 | 0 |
| **Total** | **75** | **22** | **2** | **5** |

A row with a BLOCKED sub-check counts as BLOCKED. The BLOCKED checks:
- **A7:** the built frontend compared with the actual `.env` values. Om can run the script in section 9.
- **G1a:** `smoke_llm`, which would go over the 80-call cap, and the `docs/database.md` lines that read `.env`.

**Acceptance criteria:** 40 of 45 PASS, 4 FAIL (US-01.3, US-02.4, US-25.1, US-52.5) and 1 BLOCKED (US-25.5,
Slack and the board, for the owner to confirm). **Exit criteria:** 6 of 9 PASS; number 7 FAILs on pull-request
authorship; 8 and 9 are for the owner to confirm.

**The 18 failures** (details in section 3):

| ID | Failure | Suggested owner |
|---|---|---|
| F-1 | Whitespace-only text crashes the intake with HTTP 500, not in the error shape (D1, D8) | Jenish |
| F-2 | A product edit leaves `category` and `category_label` disagreeing (D2) | Jenish, with Jhanvi |
| F-3 | `SearchResult.saved` is always `true`, so "This result isn't saved" never shows in live mode (E4, US-52.5) | Pal, with Vaishvi |
| F-4 | "USB-C/Thunderbolt" becomes "usb-c/thunderbolt" in its question (E7.L) | Jenish, with Manu |
| F-5 | `main` is not protected, and no merge had a review (G2b, US-25.1) | Palak, with Harshkumar |
| F-6 | All 36 merged pull requests are under Om's name; the other 9 members have none (G2, exit criterion 7) | Om |
| F-7 | Live mode shows a disputed value without either value or source (E2.1x) | Jenish, then Yashkumar |
| F-8 | "Estimated" has no coverage percentage (E2.3) | Manu |
| F-9 | Ties: no "Too close to call", no separating criterion, "#=1" twice (E2.4, E2.4x) | Yashkumar |
| F-10 | "Couldn't check" has no link to research it (E2.6) | Manu |
| F-11 | Old prices never say "may have changed" (E2.8) | Yashkumar |
| F-12 | No store-search link when there is no offer (E2.9) | Yashkumar, with Jenish |
| F-13 | Serious contrast failures while text is fading in (E5.3) | Manu |
| F-14 | At 390 px, focus can sit under the open menu (E5.7) | Yashkumar |
| F-15 | At 390 px, Escape on "Stop research?" loses focus (E5.9) | Manu |
| F-16 | Mobile Performance 72 to 80 and LCP 3.5 to 4.5 s, against 90 and 2.5 s (E6.1, E6.4, O9) | Manu, with Yashkumar |
| F-17 | The not-found page's heading is "404" (E7.2) | Yashkumar |
| F-18 | The privacy page says only the server can read saved requests (E7.P3) | Manu, with Om |

**Also for the team:** Groq's free tier (8,000 tokens a minute) is tight, and the Cerebras account answers 402, so
private text has no working second provider (observation O-B4). Live-call ledger: at most 78 LLM calls, and 0
Serper, YouTube and Reddit calls (section 5). Supabase: 5 rows created, listed with a delete statement for Om
(section 6).

### How it was run, and where it differs from the plan

- **S0.1, set-up from the docs:** a clean clone of the GitHub repository at `72d0433`, installed exactly as
  `README.md` and `docs/setup.md` say. It used no real keys (`.env` copied from `.env.example`, as the README says).
- **Live checks:** a fresh worktree of the same commit, inside the repository folder. There the backend finds the
  root `.env` by walking up, as it does for every member.
- **The `.env` file was never read, copied or edited by hand.** This machine's command reviewer refuses any read
  of `.env` values. So every drill changed one variable in the environment of that one command only (for example
  `GROQ_API_KEY=` or `DATABASE_URL=` pointing at a closed port), and environment variables always win over `.env`.
- **Ports:** 8000 was held by a stray server on this laptop, so the backends ran on 8001 to 8009 and the
  frontends on 3001 to 3210. The commands are otherwise the documented ones.
- **Live calls:** made-up shopping requests only. The backend I started logged every LLM call record (task, data
  class, provider, tokens, time, outcome) through a logging config kept outside the repository. The ledger is
  section 5.
- **Supabase:** only read-only queries, through the project's dashboard API, plus the app's own request saving.

## 2. Results by part

### S0. Set-up and inventory

| ID | What was checked | Result | Evidence | File |
|---|---|---|---|---|
| S0.1 | Clean clone and install exactly as the README and setup.md say | PASS | Every step worked as written: clone, `core.autocrlf`, venv, `pip install -e ".[dev]"`, `.env` from the example, the CLI (ranked list), `uvicorn` with `/api/health` "ok", `pip install -r ml/requirements.txt` and the root `requirements.txt`, the ML scripts (`match_baseline` exits 2 with no raw data, as documented), `npm ci`, `npm run dev` (GET / 200), `doctor.py` (no FAIL; it correctly shows "LLM keys: none set"), and `dev.py --mock` (GET / 200) | [s0-clean-clone.txt](sprint-1-verification/s0-clean-clone.txt) |
| S0.2 | Versions and the CI run for the commit | PASS | As in the header; all 5 CI jobs green | [s0-environment.txt](sprint-1-verification/s0-environment.txt) |
| S0.3 | `python scripts/doctor.py` | PASS | No FAIL. 5 of 5 LLM keys set (names only); database reachable, Postgres 17; contracts and frontend types in sync. WARN only for mock mode and the busy port 8000 | [s0-doctor.txt](sprint-1-verification/s0-doctor.txt) |
| S0.4 | Inventory | PASS | 9 API routes. 8 search stages, all US-52 placeholders. Intake, criteria and interview are real. 9 LLM tasks, of which Sprint 1 code calls 2: `detect_intent`, which is personal, and `generate_criteria`. 5 providers with their training flags and routes. 56 contract schemas. 13 frontend route files. 2 migrations, the same as the live history (C1) | [s0-inventory.txt](sprint-1-verification/s0-inventory.txt) |

### Part A. Static and automated checks

| ID | What was checked | Result | Evidence | File |
|---|---|---|---|---|
| A1 | Backend: ruff, ruff format, mypy, pytest with coverage, every skip, contract tests | PASS | All clean; 1,020 passed, coverage 97.49%. The 8 skips all have a reason: 5 need `TEST_DATABASE_URL`, 3 are live tests. Contract tests: 95 passed | [a1-backend.txt](sprint-1-verification/a1-backend.txt) |
| A2 | Contract drift and the generated frontend types | PASS | The export shows no drift. `gen-frontend-types.sh` (656 s) produced 56 type files identical to the ones the build uses | [a2-contracts.txt](sprint-1-verification/a2-contracts.txt) |
| A3 | Migrations and db tests on a local `postgres:17` | PASS | Both migrations apply in order on a blank database, including the guarded revoke where `rls_auto_enable()` does not exist. 5 db tests passed. With the container stopped and `REQUIRE_DB_TESTS=1`: 5 errors ("REQUIRE_DB_TESTS=1 but … cannot be reached"). Without it: 5 skipped, with that reason | [a3-database-tests.txt](sprint-1-verification/a3-database-tests.txt) |
| A4 | Frontend: lint, typecheck, `tsc`, tests, build, every route of the built server | PASS | All exit 0, with 107 tests. Every real route returns 200; `/searches`, `/saved` and an unknown path return 404 | [a4-frontend.txt](sprint-1-verification/a4-frontend.txt) |
| A5 | The existing end-to-end specs, mock mode | PASS | 36 passed, 2 skipped by design: the live spec needs `LIVE_E2E=1`, and the hidden-tab check cannot run headless | [a5-e2e.txt](sprint-1-verification/a5-e2e.txt) |
| A6 | The ML job's CI commands; raw-data checksums; the baseline re-run | PASS | Lint, strict mypy, 118 tests, the label check, kappa 0.859 (at least 0.8), and 0 raw or model files tracked. All 60 raw files match `checksums.sha256`. The baseline re-run reproduces all 16 rows of `ml/results/matching_baseline.json` (only the timing differs) | [a6-ml.txt](sprint-1-verification/a6-ml.txt) |
| A7 | Secrets and hygiene | PASS, one sub-check BLOCKED | TruffleHog 3.99.8 over the history of `main`: 0 verified and 0 unverified. No `.env`, `node_modules`, `.output`, weights or raw data is tracked, and `.gitignore` covers each. The built bundle has no key-shaped string. **BLOCKED:** comparing the bundle with the actual `.env` values, because this machine refuses any read of `.env` values. Om can run the script in section 9 | [a7-secrets-hygiene.txt](sprint-1-verification/a7-secrets-hygiene.txt) |
| A8 | CI parity | PASS | Every job on `72d0433` passed, and the local runs agree: same tests, same coverage, no drift, the same secret scan | |

### Part B. LLM providers and the wrapper

| ID | What was checked | Result | Evidence | File |
|---|---|---|---|---|
| B1 | The smoke test for every LLM provider | PASS (Cerebras account open) | Groq, Gemini, OpenRouter and Mistral answered (454 to 3,610 ms). The reasoning models needed the documented 200-token retry. **Cerebras still answers HTTP 402**: the account has no plan (`docs/api-setup.md`: "Not OK, no free tier"). This is an account decision for the team, not a code fault | [b1-smoke.txt](sprint-1-verification/b1-smoke.txt) |
| B2 | One live call of each Sprint 1 task | PASS | `detect_intent`: private, Groq, 163 output tokens, 703 ms, schema passed first time. `generate_criteria` (gaming laptop): private, Groq, 2,374 output tokens, 5.1 s, schema passed first time: 9 criteria, value for money included | [b2-tasks.txt](sprint-1-verification/b2-tasks.txt) |
| B3 | Privacy routing | PASS | Over every live call: 0 personal or private calls reached Gemini, Mistral or OpenRouter. `detect_intent` went to Groq or Cerebras (private). The one public call (Mistral) carried only a category label from our lexicon. The 7 named routing and privacy tests (24 cases) pass | [b3-privacy-routing.txt](sprint-1-verification/b3-privacy-routing.txt) |
| B4 | One key off, each in turn | PASS | Every run answered and named its source. **Groq off:** Cerebras failed after 9.3 s, so intake fell back to rules within its 5 s limit, and criteria fell back at 10 s. **Gemini, OpenRouter or Cerebras off:** no change, Groq answered. **Mistral off:** Groq was rate-limited (HTTP 429) by then, so rules and the fallback set answered. See observation O-B4 | [b4-one-key-off.txt](sprint-1-verification/b4-one-key-off.txt) |
| B5 | Every LLM key off | PASS | Intake by rules found the category and budget. Criteria came from the fallback (headphones) or a saved set (laptop). The interview ran in fixed order to a finished rubric, and the search returned a valid result. "watch" still asks which kind. 0 LLM call records | [b5-every-key-off.txt](sprint-1-verification/b5-every-key-off.txt) |
| B6 | Wrapper behaviour, from the tests | PASS | Named tests for each behaviour: context fitting (trim, chunk, re-fit on a smaller window, the oversized-input test); invalid output retried once, then the next provider, then the stage's safe default; failures moving on; and the call log (provider, tokens, time, outcome, never the prompt). 226 LLM tests pass | [b6-wrapper-tests.txt](sprint-1-verification/b6-wrapper-tests.txt) |
| B7 | The criteria output limit, five use-case requests | PASS | 4 of 4 requests that reached criteria were **generated**, none fell back: laptop for travel, phone for photography, earbuds for running, standing desk for a home office, using 988 to 2,149 output tokens. "monitor for programming" was asked "Computer monitor or Baby monitor?" first. The earlier 1,024-token limit (fixed in PR #33) no longer bites | [b7-criteria-use-case.txt](sprint-1-verification/b7-criteria-use-case.txt) |

**Observation O-B4, not a code fault:** Groq's free tier allows 8,000 tokens a minute, and one gaming-laptop criteria
answer uses about 3,100. Two or three searches in a minute therefore push the next shopper onto the keyword rules and
the fallback criteria. The app handles it as designed (cool-down, fallback), but with Cerebras unusable, private
text has no second provider. A team decision for Sprint 2 (US-46 owner Jhanvi, with Om).

### Part C. Database

| ID | What was checked | Result | Evidence | File |
|---|---|---|---|---|
| C1 | Read-only state of the Supabase project | PASS | Row level security on; 0 policies. `anon` and `authenticated` have no select or insert, and cannot execute `rls_auto_enable()`. The migration history equals the 2 files. 0 rows before the runs | [c1-database-readonly.txt](sprint-1-verification/c1-database-readonly.txt) |
| C2 | Database on | PASS | One intake created exactly one row, `sess_ec2591affc503695df73f5e6` (`sess_` plus 24 hex), with `intent_source` `llm`. `check_request_saved.py` printed "saved"; the session reported `request_saved` saved; health "ok" | [c2-database-on.txt](sprint-1-verification/c2-database-on.txt) |
| C3 | Database off | PASS | The whole flow completed; health "off"; `request_saved` off. Nothing written: after backend A's intakes the table still held only C2's row | [c3-database-off.txt](sprint-1-verification/c3-database-off.txt) |
| C4 | Database down (closed port) | PASS | Health "down"; `request_saved` failed; the whole flow completed. The slowest request took 2,187 ms, inside 5 s plus 2 s. After 3 failures the log says "skipping the database for 30 s": the next 5 intakes took 0 to 16 ms, and after 31 s the next one tried again (2,015 ms) | [c4-database-down.txt](sprint-1-verification/c4-database-down.txt) |
| C5 | No database detail in any response | PASS | No response in C3 or C4 contained a stack trace, path, connection string or database error. The logs show only the host and the error type, by design | [c4-database-down.txt](sprint-1-verification/c4-database-down.txt) |

### Part D. Backend features, story by story

| ID | What was checked | Result | Evidence | File |
|---|---|---|---|---|
| D1 | US-01 request rules | **FAIL** (1 of 8 sub-checks) | 1,000 characters accepted, 1,001 refused ("Please check: text - String should have at most 1000 characters."). Empty text: 422 with a plain sentence. "something for my mom": 4 category buttons. An unclear reply: example requests, no category. Criteria and interview refuse cleanly (409) before a category exists. A pram request works. **Whitespace-only text (`"   "`) returns HTTP 500 "Internal Server Error"**, see F-1. The browser blocks empty and whitespace input before sending (`request.test.ts`) | [d1-us01.txt](sprint-1-verification/d1-us01.txt) |
| D2 | US-02 detection and edits | **FAIL** (1 of 11 sub-checks) | All 7 requests detected correctly with LLMs on: gaming laptop ₹80,000; ₹ sets India; 1.5 lakh is ₹1,50,000; "20k tak" is ₹20,000; no budget assumed; "watch" asks; "16 GB RAM, no refurbished" gives a hard rule and an exclusion. Every budget equals the code's own parser. With every LLM off, the rules still find the category and budget (B5). PUT `/intent`: use, budget and region edits persist; a negative budget is refused in the error shape. **A product edit is accepted and leaves the intent inconsistent** (`category` smartphone, `category_label` "Laptop"), see F-2. Must-have and leave-out edits in the shape the shopper would type are refused with a technical message (known gap, Sprint 3) | [d2-us02-llm-on.txt](sprint-1-verification/d2-us02-llm-on.txt), [b5-every-key-off.txt](sprint-1-verification/b5-every-key-off.txt) |
| D3 | US-03 criteria | PASS | Laptop, headphones, standing desk and the never-seen telescope: 7 to 10 criteria each, value for money always present, unique keys, a direction each, one-line definitions. (My check flagged "CPU performance" and "GPU performance" for the word "performance"; they name a component, so they are not vague like "quality".) Gaming and travel laptops get clearly different criteria. The second telescope request came from the saved set with no criteria call. The retry-then-fallback tests pass (6) | [d3-us03.txt](sprint-1-verification/d3-us03.txt) |
| D4 | US-05 interview | PASS | One question per turn; quick replies Low, Medium, High and Skip, plus free text ("7"); unclear text gets a rephrase. Order equals the default-weight order. The finished rubric's shares sum to 1.000000 and carry the hard rule and exclusion. Going back and changing the first answer updates it (2 → 8). A reload restores the interview; bad or malformed session IDs get a clean 404. 33 turns: p50 0 ms, p90 16 ms, max 16 ms. "under 30k skips premium questions": no live criteria set has a premium-word key, so the rule was checked by its tests (8 pass) | [d4-us05.txt](sprint-1-verification/d4-us05.txt) |
| D5 | US-52 search and CLI | PASS | `POST /api/search` returns a valid `SearchResult`; all 8 stage reports with status and time; 16 ms, far under 150 s. The CLI prints a ranked list with sources, a filtered product and a "can't tell" one. It finishes with the database off (C3) and down (C4). The contract tests (95) and the fault tests (one per search stage, PR #35) pass | [d5-search.txt](sprint-1-verification/d5-search.txt) |
| D6 | Known backend gaps | N/A (record) | See section 8 | [d6-known-gaps.txt](sprint-1-verification/d6-known-gaps.txt) |
| D7 | CORS | PASS | `http://localhost:3000` (the dev server's and built server's origin) passes the preflight and a real request. Other origins get no CORS headers; `*` never appears | [d7-cors.txt](sprint-1-verification/d7-cors.txt) |
| D8 | Every 4xx and 5xx keeps the error shape and leaks nothing | **FAIL** (1 response) | 14 non-2xx responses seen. 13 are `{"detail": "<plain sentence>"}` with no leak. The whitespace-only intake (F-1) returns the plain text "Internal Server Error", not the error shape. It still leaks nothing | d1 to d4 files |

### Part E. Frontend

E1, E2, E5, E6 and E7 ran in mock mode on a production build. The full tables, notes and every evidence link are in
[part-E-mock.md](sprint-1-verification/part-E-mock.md), and the summary rows are below. E3, E4 and E8 ran in live
mode: a production build made in the clean clone with `VITE_API_MODE=live` and `VITE_API_BASE_URL=http://localhost:8009`,
served by node on port 3210, against backend E on 8009 (`FRONTEND_ORIGINS=http://localhost:3210`).

| ID | What was checked | Result | Evidence | File |
|---|---|---|---|---|
| E1 | Mock walk of the whole flow at 390 and 1440 px, light and dark | PASS (8 PASS, 1 N/A) | All 10 steps in all 4 configurations. Dark mode was set with the app's own header toggle, and every light and dark pair differs. 40 JPEGs of 18 to 151 KB. 0 console errors and 0 page errors in the four walks. The N/A is the record of console messages in the other runs: only the deliberate 404s, and a font host this machine's DNS briefly could not reach | [part-E-mock.md](sprint-1-verification/part-E-mock.md), E1 |
| E2 | Every state renders | **FAIL** (12 PASS, 7 FAIL) | **PASS:** disputed (mock), unknown ("Not verified", never blank or 0), filtered out with the reason, partial with `coverage_note` at the top, not saved (mock), the six interview states (question, answer then repeat, rephrase, redirect, skipped, finished), and the other result states. **FAIL:** disputed through the live adapter shows neither value nor source (E2.1x); "Estimated" has no coverage percentage (E2.3); a tie shows no "Too close to call" and no separating criterion, and the example shows "#=1" on both (E2.4, E2.4x); "Couldn't check" has no link to research it (E2.6); old prices never say "may have changed" (E2.8); no store-search link when there is no offer (E2.9) | [part-E-mock.md](sprint-1-verification/part-E-mock.md), E2 |
| E5 | Accessibility | **FAIL** (10 PASS, 3 FAIL, 1 N/A) | axe with `wcag2a`, `wcag2aa`, `wcag21aa` and `wcag22aa` on 204 states, in three motion modes. With reduced motion, and with motion on once fades end, there are 0 violations of any impact. With motion on and axe run as each state appears: 52 serious `color-contrast` violations on text still fading in (E5.3). Keyboard walk: 1,652 focus stops, focus always visible, no trap. But at 390 px focus can sit fully under the open menu (E5.7), and Escape on "Stop research?" drops focus to the page body (E5.9). Targets: 0 under 44 px. Token contrast test: 11 passed. 19,053 text elements at 4.5:1 or more. Nothing scrolls sideways at 320 px. The N/A is `e2e/quality`, run in A5 | [part-E-mock.md](sprint-1-verification/part-E-mock.md), E5 |
| E6 | Lighthouse 13.5.0, mobile (open item O9) | **FAIL** (3 PASS, 2 FAIL, 2 N/A) | Performance medians 72 (home), 79 (`/research`) and 80 (results address), all under 90. LCP 4.50, 3.86 and 3.55 s, all over 2.5 s. Accessibility 100 and Best Practices 100 everywhere; CLS 0.094 at most. See section 7 | [part-E-mock.md](sprint-1-verification/part-E-mock.md), E6 |
| E7 | Copy and the privacy page | **FAIL** (8 PASS, 2 FAIL) | Titles keep their case; there is no "undefined", "null" or "NaN"; rupee amounts use Indian grouping, lakh amounts included. **FAIL:** the not-found page's heading is "404" (E7.2, backlog B5). The privacy page says "Only the ShopSense server can read them", but the team can read the table (E7.P3) | [part-E-mock.md](sprint-1-verification/part-E-mock.md), E7 |

| ID | What was checked | Result | Evidence | File |
|---|---|---|---|---|
| E3 | Live walk with the database on | PASS | The URL carries the backend's session ID `sess_99e1359e5c05af6c2f9fd239` at every step. 18 API calls, all 2xx; no response failed the app's own contract checks, 0 console errors. Quick replies on screen equal the backend's (Low, Medium, High; Skip is the separate "Skip this question" button). The use, budget and region edits persisted (GET session: "web development", 75,000, IN). The product edit shows "We can't change this here yet" and is not sent. A reload restores the same question. Changing the first answer (CPU performance) updates the history, and the next question on screen matches the backend's. Back and Forward move one step at a time (questions → criteria, brief → questions, results → brief) | [e3-live-walk.txt](sprint-1-verification/e3-live-walk.txt) |
| E4 | Live failure paths | **FAIL** (1 of 4 sub-checks) | **Backend stopped:** an alert that says what to do, the typed text kept, and a Retry button that keeps it (PASS). **Every key off:** "Our backup reader filled this in. Please check it." on the understood screen; results in 6.3 s (PASS). **Database off:** the "This result isn't saved." notice never appears, because the backend reports `saved: true` (FAIL, see F-3). **Research past 170 s:** N/A, since nothing in the code can delay the placeholder search. A first run took 3.3 minutes, but the backend log shows the app's own requests took 9 s; the rest was spent before the first request, in the test's page-load wait (2.1 s when timed alone; cause not found) | [e4-failure-paths.txt](sprint-1-verification/e4-failure-paths.txt), [e4-backend-stopped.png](sprint-1-verification/e4-backend-stopped.png) |
| E7.L | Copy in live mode (seen in G1) | **FAIL** | The cached laptop criteria include the title "USB-C/Thunderbolt". The question reads "How much do you care about usb-c/thunderbolt?". Both sentence rules lower-case any word whose letters are not all capitals, see F-4 | [g1-documented-commands.txt](sprint-1-verification/g1-documented-commands.txt) |
| E8 | Only public `VITE_` variables in the build | PASS | Source and built bundle use only `VITE_API_MODE` and `VITE_API_BASE_URL`; no key-shaped string in the bundle. The comparison with the actual `.env` values is BLOCKED, as in A7 | [e8-public-env.txt](sprint-1-verification/e8-public-env.txt) |

### Part F. The whole system

| ID | What was checked | Result | Evidence | File |
|---|---|---|---|---|
| F1 | `python scripts/dev.py` and the doctor | PASS | `dev.py` started the backend and frontend in live mode and printed the health summary every time. The doctor showed no FAIL (S0.3) | [f2-live-runs.txt](sprint-1-verification/f2-live-runs.txt) |
| F2 | The live end-to-end test, database on and off | PASS | **On:** `sess_3a380586368d7289d5b2c29d`, understood 9.0 s, criteria 0.2 s, 9 answers 4.1 s, brief 0.2 s, results 0.6 s; "saved". **Off:** `sess_972c64171b5a855e8a9fc425`, 6.7 s, 0.2 s, 4.0 s, 0.2 s, 0.5 s; "not found" | [f2-live-runs.txt](sprint-1-verification/f2-live-runs.txt) |
| F3 | The drills in `docs/setup.md` | PASS | **Groq off:** 4 of 5 keys; the live test passed. **Database at a closed port:** health "down"; passed. **Every key off:** 0 of 5 keys; passed. No crash in any run | [f3-drills.txt](sprint-1-verification/f3-drills.txt) |
| F4 | Five live journeys in the browser | PASS (known gap) | Every journey reached results, with 0 failed API calls and 0 console errors: laptop for video editing (8 turns, 16 s), phone for my father (10, 16 s), gym headphones (8, 12 s), standing desk (9, 13 s), "something for my mom" via the follow-up "Mixer grinder" (8, 18 s). **Wrong along the way:** every category ends with the same example laptops (the placeholder search; known gap, section 8) | [f4-journeys.txt](sprint-1-verification/f4-journeys.txt) |
| F5 | The Sprint 1 exit criteria | See below | | |

#### F5. Sprint 1 exit criteria

| # | Exit criterion | Result | Proved by |
|---|---|---|---|
| 1 | The CLI and the browser both run a request through every stage and show a ranked list with sources | PASS | D5 (CLI and `POST /api/search`, 8 stage reports), F2 and F4 (browser, live), E3 |
| 2 | Every stage has a contract and a contract test; a failing stage triggers its fallback | PASS | A1 (95 contract tests), D5 (one fault test per search stage), B4 and B5 (intake and criteria fallbacks, live) |
| 3 | Detection shows category, use, budget, region and rules; "watch" asks; with every LLM off the keyword rules still find the category and budget | PASS | D2, B5, E4 |
| 4 | Criteria with definitions for three categories, one never seen, and the fallback set when generation fails | PASS | D3 (laptop, headphones, standing desk, telescope), B4 and B5 (fallback set) |
| 5 | The interview runs at least 5 questions and stores the answers | PASS | D4 (33 turns, reload restores), F4 (8 to 10 turns per journey), E3 |
| 6 | Switching off one LLM key does not stop the app | PASS | B4 (each of the 5 keys in turn), F3 (Groq off) |
| 7 | CI runs lint, types and tests on every pull request; no secret in the repository; every one of the 10 members has a merged pull request under their own name | **FAIL** | CI on every pull request and push (`ci.yml`, run 41871458849): PASS. No secret (A7): PASS. **All 36 merged pull requests were opened by `Om-5640`; the other 9 members have none** (G2), see F-6 |
| 8 | The survey has 30 or more responses; the interviews, observation, expert interview and API limits are written up | Owner confirms | Not code |
| 9 | The poster is printed | Owner confirms | Not code |

### Part G. Documentation and credit

| ID | What was checked | Result | Evidence | File |
|---|---|---|---|---|
| G1a | Every command in `README.md`, `docs/setup.md`, `docs/api-setup.md`, `docs/database.md` and `frontend/README.md` | PASS for every command run; 2 BLOCKED, 6 N/A | Every command that was run worked as written (S0 to F, and the API walk-throughs run again here). **BLOCKED:** `python -m scripts.smoke_llm`, because 5 to 8 more calls would pass the 80-call cap (B1 covered the same providers); and the `export DATABASE_URL=…dotenv_values(".env")…` lines in `docs/database.md`, because this machine refuses any read of `.env` values. **N/A under this check's rules:** `doctor.py --ping`, `smoke_apis` in its default, `--only groq,youtube` and `--include serper` forms, and `smoke_sources` (they call YouTube, Reddit archives or Serper); `supabase migration new` and `db push` (they would change a migration or the team's database). Observation: the 8 answers in "A whole interview in one go" do not finish a 9-question interview | [g1-documented-commands.txt](sprint-1-verification/g1-documented-commands.txt) |
| G1b | OpenAPI routes against the documented routes | PASS | The same 9 routes in the OpenAPI and in `docs/frontend-api.md` with `docs/api/presearch.md`. The docs write the path parameter as `{id}` in some places and `{session_id}` in others | [g1-documented-commands.txt](sprint-1-verification/g1-documented-commands.txt) |
| G2 | Credit: the GitHub author of every commit on `main`; merged pull requests per member | **FAIL** (exit criterion 7) | Every member has commits linked to their own GitHub account. 17 commits carry an older address that is not on the member's account (Jenish 12, Pal 5). Every merged pull request was opened and merged by `Om-5640` | [g2-credit.txt](sprint-1-verification/g2-credit.txt) |
| G2b | US-25: `main` protected, one review required per merge | **FAIL** | GitHub reports `main` as `protected: false` with no required checks. The rulesets API answers "Upgrade to GitHub Pro or make this repository public". 0 of the 36 merged pull requests has an approving review. The repository has 10 collaborators (PASS). See F-5 | [g2-credit.txt](sprint-1-verification/g2-credit.txt) |

**Credit on `main` (165 commits up to `72d0433`)**

| Member | GitHub login | Linked commits (not merges) | Merged pull requests under their own name | Commits with an unlinked address |
|---|---|---|---|---|
| Om Pandya | `Om-5640` | 16 (plus 36 pull request merges) | 36 | 0 |
| Jhanvi Barot | `JhanviBarot` | 24 | 0 | 0 |
| Manu Pardhan | `manupardhan05` | 21 | 0 | 0 |
| Palak Patel | `Palak178-creator` | 15 | 0 | 0 |
| Vaishvi Mehta | `VaishviMehta346` | 8 | 0 | 0 |
| Pal Jalodara | `paljalodara` | 7 | 0 | 5 |
| Jenish Kavad | `JenishKavad` | 6 | 0 | 12 (11 commits and 1 merge of `main` into a branch) |
| Yashkumar Patel | `yashpatel4239` | 6 | 0 | 0 |
| Rushiraj Navar | `Rushinavar18` | 5 | 0 | 0 |
| Harshkumar Kodavala | `202401121-cmyk` | 4 | 0 | 0 |

## 3. Failures

**F-1. Whitespace-only text crashes the intake with HTTP 500.** Checks D1 and D8. Story US-01.
- **Expected:** US-01.3, "Empty or unclear text gets one clear follow-up question, not an error". Also the error shape:
  every 4xx and 5xx is `{"detail": "<plain sentence>"}`.
- **What happened:** `{"text": "   "}` returned HTTP 500 with the plain text "Internal Server Error". Empty text
  (`""`) correctly gets a 422 with a sentence.
- **Steps:** start the backend, then
  `curl -s -X POST http://localhost:8000/api/presearch/intake -H "Content-Type: application/json" -d '{"text": "   "}'`.
- **Cause (confirmed by the traceback in d1-us01.txt):** `IntakeRequest.text` accepts 1 to 1,000 characters, so
  three spaces pass. `start_request` then builds `IntakeInput` (`app/presearch.py` line 57), which strips the
  spaces and rejects the empty string. That `ValidationError` is not caught in `intake` (`app/api/presearch.py`
  line 115).
- **Suggested owner:** Jenish (pre-search API).

**F-2. A product edit leaves the intent disagreeing with itself.** Check D2. Story US-02.
- **Expected:** US-02.4, "a validated Intent shown back to the user, who can edit it".
- **What happened:** `PUT /api/presearch/{id}/intent` with a new category was accepted. The session then held
  `category` smartphone with `category_label` "Laptop". (The browser does not send product edits; it says "We can't
  change this here yet", E3.)
- **Steps:** intake "laptop for coding under 70k", then `PUT …/intent` with `{"category": "smartphone"}` (200), then
  `GET` the session.
- **Cause (confirmed by the response and the code):** `revise_intent` (`app/intake/stage.py` line 104) merges the
  changes into the old intent and re-validates. No rule ties `category_label` (or the criteria already made) to
  `category`.
- **Suggested owner:** Jenish (pre-search API), with Jhanvi (intent detection).

**F-3. `SearchResult.saved` is always `true`, and nothing is saved at the end.** Check E4, criterion US-52.5, known
gap 4. Story US-52.
- **Expected:** US-52.5, "finishes without the database and saves at the end". The field meaning: `saved` is about
  results. The contract's own description says "False if the database was down (banner in the UI)".
- **What happened:** with the database off (C3, E4) and down (C4), every search answered `saved: true`. So the
  results page never shows "This result isn't saved". Mock mode does show it (E2.10).
- **Steps:** run the backend with `DATABASE_URL` unset, run any search, and read `saved`.
- **Cause (confirmed by code search):** `saved` defaults to `True` (`app/contracts/pipeline.py` line 93), and no code
  in `backend/app` sets it. Saving results is not built yet.
- **Suggested owner:** Pal (walking skeleton and pipeline), with Vaishvi (database).

**F-4. A criterion title with a slash is lower-cased in its question.** Check E7.L, seen in G1. Decision: no criterion
title is lower-cased.
- **What happened:** the cached laptop set has the title "USB-C/Thunderbolt". The question reads "How much do you
  care about usb-c/thunderbolt?".
- **Steps:** with that criteria set, answer the interview until this criterion comes up (g1-documented-commands.txt).
- **Cause (confirmed by the code):** both `in_sentence` (`backend/app/interview/questions.py` line 30) and
  `inSentence` (`frontend/src/lib/text.ts` line 5) keep a word only when every letter in it is a capital.
  "USB-C/Thunderbolt" is one word with lower-case letters, so all of it is lower-cased. The same happens to a brand
  or model name such as "iPhone" or "MacBook".
- **Suggested owner:** Jenish (interview API), with Manu (interview chat).

**F-5. `main` is not protected, and no merge had a review.** Check G2b. Story US-25.
- **Expected:** US-25.1, "a protected main, and one review required per merge".
- **What happened:** GitHub reports `main` as `protected: false` with no required checks. 0 of the 36 merged pull
  requests has an approving review.
- **Cause (suspected):** the repository is private on a personal account without GitHub Pro. GitHub's answer to
  the rulesets query is "Upgrade to GitHub Pro or make this repository public to enable this feature".
- **Suggested owner:** Palak (CI and repository rules), with Harshkumar, whose account owns the repository. The
  options are to make it public, move it to a free organisation with GitHub Education, or upgrade. This is a team
  decision.

**F-6. Nine members have no merged pull request under their own name.** Check G2, exit criterion 7.
- **Expected:** "every one of the 10 members has at least one merged pull request under their own name".
- **What happened:** all 36 merged pull requests were opened and merged by `Om-5640`. Every member does have commits
  linked to their own account.
- **Suggested owner:** Om. From Sprint 2, each member opens the pull request for their own story.

**F-7 to F-18: frontend.** Each has its expected result, what happened, steps, cause and story in
[part-E-mock.md](sprint-1-verification/part-E-mock.md), "Failures" (numbered F1 to F12 there).

| ID | Check | Failure | Cause | Story | Suggested owner |
|---|---|---|---|---|---|
| F-7 (F1) | E2.1x | In live mode a disputed value shows only "Sources disagree", with neither value nor source | Confirmed: `SearchResult` carries claim IDs, not the claims, so the adapter has nothing to show | US-52, FE-0 | Jenish (contract change, a team decision), then Yashkumar |
| F-8 (F2) | E2.3 | "Estimated" has no coverage percentage (known follow-up) | Confirmed: `CriterionRow` renders only the badge; `CriterionValue` has no coverage field | US-52, FE-0 | Manu |
| F-9 (F3) | E2.4, E2.4x | Ties: no "Too close to call", no separating criterion, and "#=1" on both tied products (known follow-up) | Confirmed: the adapter does not read `separating_criterion` | US-52, FE-0 (US-19) | Yashkumar |
| F-10 (F4) | E2.6 | "Couldn't check" has no link to research the product | Confirmed: the list renders the name and reason only | US-52, FE-0 | Manu |
| F-11 (F5) | E2.8 | Old prices never say "may have changed"; mock staleness is a fixture flag, not the 24-hour rule | Confirmed: `OfferRow` wording; `mockService.ts` sets `stale` by hand | US-52, FE-0 (DR-04) | Yashkumar |
| F-12 (F6) | E2.9 | No store-search link when there is no offer (known follow-up, O2) | Confirmed: the contract has no such link | US-52, FE-0 | Yashkumar, with Jenish for the link |
| F-13 (F7) | E5.3 | 52 serious contrast violations while text is still fading in (motion on) | Confirmed: reveal and entrance animations hold text at partial opacity for about half a second | FE-4 | Manu |
| F-14 (F8) | E5.7 | At 390 px with the menu open, focus can sit fully under the sticky header (WCAG 2.4.11) | Suspected: the menu stays open while focus moves through the page | FE-4 | Yashkumar |
| F-15 (F9) | E5.9 | At 390 px, Escape on "Stop research?" drops focus to the page body | Confirmed: the menu link that opened it is gone, and the dialog has no fallback | FE-4 | Manu |
| F-16 (F10) | E6.1, E6.4 | Mobile Performance under 90 and LCP over 2.5 s on every page (O9) | Confirmed by Lighthouse: the render-blocking Google Fonts stylesheet and app CSS hold the first paint | FE-4 | Manu, with Yashkumar for font loading |
| F-17 (F11) | E7.2 | The not-found page's heading is "404" (backlog B5) | Confirmed: `NotFoundComponent` in `src/routes/__root.tsx` | FE-0 | Yashkumar |
| F-18 (F12) | E7.P3 | The privacy page says only the server can read saved requests | Confirmed: wording. The team reads the table in the dashboard (`docs/database.md`) | US-01 | Manu, with Om to agree the wording |

## 4. The 45 Sprint 1 acceptance criteria

From `backlog/stories.yaml`: US-01 7, US-02 5, US-03 6, US-05 5, US-25 5, US-26 11, US-52 6. That is 45, as expected.
**40 PASS, 4 FAIL, 1 BLOCKED.**

| Criterion | Result | Proved by |
|---|---|---|
| US-01.1 One text box takes up to 1,000 characters; nothing else needed to start | PASS | D1 (1,000 accepted, 1,001 refused), E1 |
| US-01.2 Works for any physical product | PASS | D1 (pram), F4 (five categories) |
| US-01.3 Empty or unclear text gets one clear follow-up question, not an error | **FAIL** | Whitespace-only text gives HTTP 500 (F-1). The browser blocks empty input itself |
| US-01.4 "something for my mom" gets 3 or 4 category buttons | PASS | D1 (4 buttons), F4 |
| US-01.5 Still unclear: example requests, never a guessed category | PASS | D1 |
| US-01.6 Research never starts until the category is known | PASS | D1 (409 before a category exists) |
| US-01.7 Each request is saved with a session ID | PASS | C2, section 6 |
| US-02.1 "Gaming laptop under 80k"; k, lakh and ₹; ₹ sets India | PASS | D2 |
| US-02.2 No budget assumed | PASS | D2 |
| US-02.3 "watch" asks which kind | PASS | D2, B5 |
| US-02.4 A validated Intent shown back and editable | **FAIL** | A product edit is accepted and leaves `category` and `category_label` disagreeing (F-2) |
| US-02.5 Every LLM fails: keyword rules still find the category and budget | PASS | B5, E4, F3 |
| US-03.1 7 to 10 category-specific criteria, none vague | PASS | D3, B7 |
| US-03.2 Value for money always included | PASS | D3, B2 |
| US-03.3 Criteria change with the use (gaming vs travel) | PASS | D3 |
| US-03.4 A never-seen category works and is generated once | PASS | D3 (telescope from the saved set the second time) |
| US-03.5 Fewer than 6: one retry, then the fallback set | PASS | D3 (6 tests), B4 and B5 (fallback set, live) |
| US-03.6 A one-line definition for each criterion | PASS | D3 |
| US-05.1 One question per turn, quick replies and free text | PASS | D4, E2, E3 |
| US-05.2 A settled topic removes related questions; "under 30k" skips premium questions | PASS | D4 (by its 8 tests; no live set had a premium question) |
| US-05.3 Go back, change an answer, later questions update | PASS | D4, E3 |
| US-05.4 Default importance order | PASS | D4 |
| US-05.5 Each turn within 5 s | PASS | D4 (max 16 ms), F2 |
| US-25.1 One repo with all 10 members, a protected `main`, one review per merge | **FAIL** | 10 members, but `main` is not protected and no merge had a review (G2b, F-5) |
| US-25.2 CI runs ruff, mypy, ESLint and the tests on every pull request | PASS | `ci.yml` (`pull_request` and `push`), A8 |
| US-25.3 Secret scanning on, `.env` ignored, keys only in GitHub Secrets | PASS | TruffleHog job on every pull request and push (A7); `.env` ignored; no key in the repository, and CI needs none (0 repository secrets). GitHub's own secret scanning is not offered for this private repository |
| US-25.4 Versioned migration files from the first table | PASS | A3, C1 |
| US-25.5 Slack channels, the Projects board and the folder layout | BLOCKED | The folder layout exists (PASS). Slack and the board cannot be seen from this machine (G2 file); the owner confirms |
| US-26.1 One function for every LLM call | PASS | B6; no provider SDK is imported anywhere in `backend/app` |
| US-26.2 Input tokens counted against the context window with a 10% margin | PASS | B6 |
| US-26.3 Trim or chunk; never over the limit | PASS | B6 |
| US-26.4 Limits in one config, checked by the smoke test | PASS | `config/models.yaml`, B1 |
| US-26.5 Re-fit on a fallback to a smaller window | PASS | B6 |
| US-26.6 Moves through the providers on rate limits, timeouts and server errors | PASS | B4 (Groq 429 moved on, live), B6 |
| US-26.7 Validate; retry once; next provider; safe default | PASS | B6, B2 (schema passed first time) |
| US-26.8 Each call logs provider, tokens, time and outcome | PASS | B2, B3, section 5 |
| US-26.9 Personal tasks never reach the Gemini free tier, with a test | PASS | B3 |
| US-26.10 An oversized-input test | PASS | B6 |
| US-26.11 A smoke test of every API records its real limits | PASS | B1 (limits and rate-limit headers) |
| US-52.1 A contract and a stub for every stage | PASS | S0.4, D5 |
| US-52.2 Runner order, time limits, fallbacks, the 150-second stop | PASS | D5, A1 |
| US-52.3 The CLI prints a ranked list with sources | PASS | D5, S0.1 |
| US-52.4 The same pipeline feeds the browser | PASS | E3, F2, F4 |
| US-52.5 Finishes without the database, and saves at the end | **FAIL** | It finishes without the database (C3, C4: PASS). Nothing is saved at the end, yet `SearchResult.saved` is `true` even with the database off or down (F-3) |
| US-52.6 A contract test for each stage; a failing stage triggers its fallback | PASS | A1, D5 (PR #35) |

## 5. Live-call ledger

The cap was 80 LLM calls. **Total: at most 78, of which 59 were counted exactly. Serper 0, YouTube 0, Reddit 0.**
Research in Sprint 1 is the placeholder, so no search, video or Reddit service is called. The documented commands
that would call one were not run (G1a).

**Calls with a call record** (the wrapper's own log line, from my backends and harness runs: 51 calls):

| Provider | Task | Data class | Calls | Outcome | Input tokens | Output tokens |
|---|---|---|---|---|---|---|
| Groq | `detect_intent` | private | 36 | 35 ok, 1 rate-limited (HTTP 429) | 22,374 | 8,205 |
| Groq | `generate_criteria` | private | 11 | 11 ok | 8,336 | 19,797 |
| Cerebras | `detect_intent` | private | 2 | 2 failed (HTTP 402, no plan) | 1,428 | 0 |
| Mistral | `generate_criteria` | public (category label only) | 2 | 2 ok | 1,436 | 1,460 |
| **Total** | | | **51** | | **33,574** | **29,462** |

**Calls without a call record:**

| Where | Calls | How counted |
|---|---|---|
| B1 smoke test | 8: Groq 2, Gemini 2, OpenRouter 2, Cerebras 1, Mistral 1 | Its own table. The three reasoning models each needed the documented second try with 200 output tokens. It prints no token counts |
| F2, F3 and F4 (`dev.py` runs) | at most 19 | `dev.py` cannot take the logging config, so I counted the requests that can call an LLM: 11 intakes and 10 criteria requests, minus the every-key-off drill (2 requests, 0 calls). That gives at most 19, counting one call each. A saved criteria set makes no call. A retry would add one, but none can be seen |

By provider: Groq 49 counted plus up to 19; Cerebras 3; Mistral 3; Gemini 2; OpenRouter 2. Gemini and OpenRouter
were called only by the smoke test, with its fixed public prompt.

## 6. Rows these runs created in Supabase

Read back on 4 November 2026 at about 18:30 UTC with one read-only `select` (`session_id`, `created_at`). The table held 0 rows before
the runs (C1) and holds exactly these 5. Every other run had the database off or down.

| session_id | created_at (UTC) | Run |
|---|---|---|
| `sess_ec2591affc503695df73f5e6` | 2026-11-04 17:57:50 | C2, database on |
| `sess_3a380586368d7289d5b2c29d` | 2026-11-04 18:00:34 | F2 run 1, database on |
| `sess_859443ed8c493331cc8c36c9` | 2026-11-04 18:01:50 | F3 drill 1, Groq off (`intent_source` rules) |
| `sess_4436e79e598b731f18eda1f6` | 2026-11-04 18:03:04 | F3 drill 3, every key off (`intent_source` rules) |
| `sess_99e1359e5c05af6c2f9fd239` | 2026-11-04 18:12:23 | E3, live walk |

For Om to review and run. It was not run here.

```sql
delete from public.requests where session_id in ('sess_ec2591affc503695df73f5e6', 'sess_3a380586368d7289d5b2c29d', 'sess_859443ed8c493331cc8c36c9', 'sess_4436e79e598b731f18eda1f6', 'sess_99e1359e5c05af6c2f9fd239');
```

## 7. O9: Lighthouse numbers and the LCP evidence

Lighthouse 13.5.0, mobile defaults (simulated slow 4G, 4x CPU slowdown), on the mock-mode production build, medians
of 3 runs. Every run warned that the machine's CPU was slower than Lighthouse expects (benchmark index 790 to 920),
because other checks were running at the same time. A quiet machine may score higher. Every run is in
[frontend/e6/lighthouse-numbers.txt](sprint-1-verification/frontend/e6/lighthouse-numbers.txt).

| Page | Performance | Accessibility | Best Practices | LCP | CLS | TBT | LCP element |
|---|---|---|---|---|---|---|---|
| `/` | 72 (70, 72, 73) | 100 | 100 | 4.50 s | 0.0003 | 0 ms | the hero paragraph `p.mt-7`, "Stop jumping between reviews, product pages and comparison videos…" |
| `/research` | 79 (79, 79, 80) | 100 | 100 | 3.86 s | 0.0001 | 50 ms | `h1` "What are you shopping for?" |
| Results address, opened cold | 80 (80, 79, 81) | 100 | 100 | 3.55 s | 0.094 | 12 ms | `h1` "We couldn't find this search" (results live only in the tab that ran the research) |
| Results, "Start research" to results shown (user flow) | 71 (timespan) | not measured | 100 | none | 0.0007 | 636 ms | none |
| Results address reloaded in the same tab | 87 (88, 87, 87) | 100 | 100 | 3.54 s | 0 | 70 ms | not extracted |

**What delays LCP on the home page**
([frontend/e6/home-lcp-evidence.txt](sprint-1-verification/frontend/e6/home-lcp-evidence.txt)):
- **LCP equals FCP** in all three runs. The hero paragraph is drawn with the first paint, from the server-rendered
  HTML.
- **Time to first byte is 12 to 14 ms.** The element render delay is 2.1 to 2.8 s, so the delay is in rendering,
  not in the server.
- **Render-blocking requests**, estimated savings 2.07 to 2.25 s:
  - the Google Fonts stylesheet (1.06 to 1.33 s);
  - the app's own CSS (about 0.15 s).

  The font files themselves use `display=swap`, so it is the stylesheet that blocks.
- **Other findings:** 45 KiB of unused JavaScript in the main bundle, and the HTML is sent without compression
  (about 19 KiB).
- **The typing animation does not delay LCP:** TBT is 0 ms, and LCP happens at first paint.

## 8. Known gaps and their status on `72d0433`

**Backend gaps from the frontend import, re-checked in D6** ([d6-known-gaps.txt](sprint-1-verification/d6-known-gaps.txt)):

| # | Gap | Status | Note |
|---|---|---|---|
| 1 | Sending the same interview reply twice answers the next question too (no idempotency) | OPEN | A double click or a network retry skips a question. Suggested owner: Jenish |
| 2 | Placeholder results use criterion keys that differ from the session's criteria | OPEN | Until the real research and decide stages replace the placeholders |
| 3 | Placeholder results carry no "sample" label in the `SearchResult` | OPEN | In live mode every category ends with the same example laptops, unlabelled (F4) |
| 4 | `SearchResult.saved` is `true` for results kept only in memory | OPEN | Failure F-3 |
| 5 | Left-out products missing from `products` | FIXED | 0 missing |
| 6 | `POST /api/search` not documented for the frontend | FIXED | `docs/frontend-api.md` |
| 7 | `IntakeResponse` and `PresearchSession` have no exported JSON Schema | OPEN | 0 of 2 in `shared/contracts/schema/`. The frontend checks their outer fields by hand (`liveAdapter.ts` line 35) and the nested `Intent` against its schema |
| 8 | No progress stream | PLANNED | Server-Sent Events from Sprint 2 (the architecture plan) |
| 9 | No way to cancel a search | OPEN | No cancel route in the OpenAPI |
| 10 | Sessions live in server memory only | OPEN | A backend restart loses every session in progress (`app/api/sessions.py` line 62) |
| 11 | Must-have and leave-out edits in the shape a shopper types are refused with a technical message | PLANNED | Sprint 3, as recorded in D2 |

**Frontend follow-ups, re-checked in E2** ([part-E-mock.md](sprint-1-verification/part-E-mock.md)):

| Follow-up | Status |
|---|---|
| A store-search link when no offer is found | OPEN: "No price found" only |
| The separating criterion for ties | OPEN: the contract carries `separating_criterion`, but the live adapter drops it and the card has no place for it |
| The coverage percentage next to "Estimated" | OPEN: an "Estimated" badge only |
| The "#=1" tie badge | OPEN: both tied products read "#=1", including the one under "Best match" |

## 9. For Om: the bundle check that was BLOCKED here (A7, E8)

This machine refuses any read of `.env` values, so the check below could not run. From the repository root, after
`npm run build` in `frontend/` (both modes, if you want to check each), run the script. It reads `.env` in memory
and prints only each variable's name and whether its value appears anywhere in the built frontend. Every line
should end in `False`.

```bash
python - <<'EOF'
from pathlib import Path
from dotenv import dotenv_values
values = {name: value for name, value in dotenv_values(".env").items() if value and len(value) >= 8}
built = [f.read_bytes() for f in Path("frontend/.output").rglob("*") if f.is_file()]
for name in sorted(values):
    print(name, any(values[name].encode() in data for data in built))
EOF
```
