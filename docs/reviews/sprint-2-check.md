# Sprint 2 review check

- **Checked by:** Palak Patel (QA), task #31, on 10 November 2026, 08:49 to 09:45 UTC.
- **Commit:** `main` at **`b6ec081`** (Merge pull request #73). CI run 42783794064 passed all 5 jobs on it
  ([c2-ci-on-main.txt](sprint-2-check/c2-ci-on-main.txt)).
- **Scope:** every Sprint 2 prompt is merged except P15, P16 and P16b, which D-61 moves to Sprint 3.
- **Nothing changed:** no application code, test, configuration, workflow, contract or migration was touched. This
  report and its evidence folder, [sprint-2-check/](sprint-2-check/), are the only new files.
- **Machine:** Windows 11, Python 3.11.15 and 3.13.13 in fresh virtual environments, Node 22.18.

Each result says PASS, FAIL or BLOCKED and names the evidence file that holds the command and its output. No check was
re-run to get a pass. The one exception is the screen walk: its script, not the app, was fixed and run again
(section 2).

## 1. Summary

**Verdict: Not yet: 14 failures (10 FAIL, 4 BLOCKED).**

| Check | PASS | FAIL | BLOCKED | Moved (D-61) |
|---|---|---|---|---|
| C1 Acceptance criteria (40) and done-lines (7 rows) | 36 | 5 | 2 | 4 |
| C2 Regression | 14 | 2 | 1 | 0 |
| C3 The demo, live | 5 | 0 | 1 | 0 |
| C4 Drills | 1 | 1 | 0 | 0 |
| C5 Rules | 5 | 0 | 0 | 0 |
| C6 GitHub | 3 | 4 | 0 | 0 |
| C7 Supabase | 3 | 0 | 0 | 0 |
| **Total** | **67** | **12** | **4** | **4** |

The 12 FAIL rows are 10 different failures. The fast-suite time fails in both C1 and C2 (F-1), and the unprotected
`main` fails two criteria (F-2).

| ID | Failure | Suggested owner |
|---|---|---|
| F-1 | The fast suite takes 64.1 s on Python 3.11; the limit is 60 s (US-32) | Harshkumar |
| F-2 | CI does not block a merge: `main` has no protection (US-32, and US-48's last criterion) | Om (reword both criteria, as US-25 was for D-19) |
| F-3 | Bulk extraction rotates across three Groq models, not across providers (US-46) | Jhanvi, or Om to reword |
| F-4 | Gemini and OpenRouter have no daily token counter (US-46) | Jhanvi |
| F-5 | Mock end-to-end: 1 of 168 tests fails, frame rate while typing 28 against at least 30 | Manu |
| F-6 | Rate-limit drill: the search moved on, but the shopper saw "Extract took too long" | Jhanvi |
| F-7 | 20 of the 24 Sprint 2 pull requests were opened by Om's account, not by their owner | Om and each owner |
| F-8 | PR #50's description names an AI tool (the old name of the root rules file) | Om |
| F-9 | Two branches from Sprint 1 still on GitHub have an AI tool in their name | Om |
| F-10 | Seven commit messages on `main`, all from before Sprint 2, name an AI tool | Om (decide: record it, since a fix rewrites `main`) |
| B-1 | The 37 database tests were not run here: Docker was not running | Om (start Docker), then Palak |
| B-2 | Filtering and ranking can't be tested offline yet: they are not built (US-32) | US-17 and US-18 owners |
| B-3 | Four of the seven parsers US-48 names don't exist yet, so they have no cases | US-20, US-39 and US-41 owners |
| B-4 | P4's hostile pages can't be shown in a live search: no live harness exists (C3) | Pal (US-28) |

**Moved to Sprint 3 by D-61 (they don't count against the verdict):**
- **#29:** matcher training data and notebook v0.
- **L2:** the own-pairs evaluation.
- **The own-pairs labelling** (L1's runbook).
- **#30 past the conflicts entered.**

Section 3 shows what exists for each one. D-61 itself is not yet in the decision register (Om).

## 2. How it was checked

- **Clean clone.** All suites ran in a clean clone of `main` at `b6ec081`:
  - a new Python 3.11 venv for the backend CI job and the ml job;
  - a new Python 3.13 venv for the Windows job;
  - `npm ci` for the frontend.
- **The live runs.** These ran from the repository on the branch `chore/sprint-2-check`, which equals `main`, through
  `scripts/dev.py` and `python -m app.cli`. All of them used one made-up request: "Laptop for coding under 80k". There
  were seven searches in six runs, because R4 ran twice:

  | Run | What it did |
  |---|---|
  | R1 | The CLI with `--claims`, database on |
  | R2 | The live end-to-end test, database on |
  | R3 | The live end-to-end test, database off |
  | R4 | A screen walk, then Stop (R4a and R4b) |
  | R5 | The rate-limit drill |
  | R6 | The Reddit-off drill |

- **Two helpers outside the repository.** Both were loaded through `PYTHONPATH` and change nothing in the app:
  - a call log, which writes one line per LLM call: task, data class, provider, outcome and token counts, never text;
  - a budget guard, which counts every outgoing call and stops any run before its cap. It refused no call.
  - `EXTRACT_MAX_SOURCES` was set per command (6 for R1, 3 for the others) to keep within the LLM budget.
  - `.env` was never edited or printed. Only the C5 scan loaded it, in memory, to compare secret values and to open
    its read-only database transaction.
- **Reddit came from the research cache.** Its threads were fetched on 9 November at 12:08 UTC, within D-53's 24
  hours. That is why the Reddit archive count is 0.
- **No Reddit text in the evidence.** The CLI prints claims with their quotes, so its output was filtered in memory:
  a claim from a Reddit source keeps its product, criterion and type, but its value and quote become a marker. Only
  the filtered text was saved. The C5 scan checks this.
- **Three changes from the plan:**
  - **The rate-limit drill.** The plan used `MISTRAL_TPM=1`. Extraction tries Gemini first and Mistral second (route
    `public.long` in `config/models.yaml`), so a Mistral limit would never be reached. Instead the guard answered
    Gemini's requests with an HTTP 429 at the network edge, as Gemini does when its limit is reached. The app's own
    429 handling ran unchanged. Reddit was off in that run, so that extraction read web and review pages.
  - **The screen walk ran twice.** The first walk script (R4a) waited too long, and the search finished before it
    pressed Stop. That was a fault in the script, not in the app. The second walk (R4b) pressed Stop while the sources
    were being read. Both are reported.
  - **The database tests.** Starting Docker Desktop was refused by this session's permission system. By the project
    rule, no other Postgres was used, so the database tests are BLOCKED (B-1).

## 3. Results by check

### C1. Acceptance criteria and done-lines

The criteria are copied from `backlog/stories.yaml`. The tests were run verbosely in the clean clone, with
`-m "not live"` so the tests marked slow run too.

| Story and criterion | Result | Proved by | File |
|---|---|---|---|
| **US-08** "Low", "medium" and "high" map to weights 2, 5 and 8 out of 10; "good enough" maps to 3 | PASS | `test_every_phrase_in_the_table_gives_its_weight` (2, 5, 8, 3), `test_the_table_is_the_one_us_08_sets`, the frontend table test | [c1-us-08.txt](sprint-2-check/c1-us-08.txt), [c1-frontend-tests.txt](sprint-2-check/c1-frontend-tests.txt) |
| US-08 The user can switch to an exact value at any time | PASS | Medium, then 7.5, then Good enough on one question; the same through the API, and after the interview finished | [c1-us-08.txt](sprint-2-check/c1-us-08.txt) |
| US-08 The mapping is a single table with unit tests, applied the same way everywhere | PASS | A code scan finds no second copy, in the backend or the frontend; the exported table equals the constant | [c1-us-08.txt](sprint-2-check/c1-us-08.txt) |
| **US-12** It builds 4 to 6 search queries from the rubric (category, use, budget, region), not from the raw text, and adds a comparison query ("X vs Y") once candidates are known | PASS | 4, 5 and 6 queries pinned; the shopper's own words never reach Serper; the comparison query takes the 6th slot, so Serper stays at 6. Live, no comparison query was sent ("fewer than two candidates"), because the match stage is still a sample | [c1-us-12.txt](sprint-2-check/c1-us-12.txt), [c3-r1-cli-claims.txt](sprint-2-check/c3-r1-cli-claims.txt) |
| US-12 It pulls from at least 3 source types: Reddit threads, web search results and review sites | PASS | Test, and live R1: 20 Reddit threads, 20 web results, 7 review pages | [c1-us-12.txt](sprint-2-check/c1-us-12.txt), [c3-r1-cli-claims.txt](sprint-2-check/c3-r1-cli-claims.txt) |
| US-12 Sources run in parallel, and one failing source never stops the run | PASS | A 3-way barrier only opens when all run at once; a source that raises leaves the others; live R6 finished with Reddit off | [c1-us-12.txt](sprint-2-check/c1-us-12.txt), [c4b-reddit-off.txt](sprint-2-check/c4b-reddit-off.txt) |
| US-12 Near-duplicate threads (more than 60% title overlap) are merged | PASS | 61% merged, 60% and 59% kept apart; the kept thread holds both URLs | [c1-us-12.txt](sprint-2-check/c1-us-12.txt) |
| US-12 Every page or thread is stored with its URL and fetch time | PASS | Tests per source type; live, every source line shows both | [c1-us-12.txt](sprint-2-check/c1-us-12.txt), [c3-r1-cli-claims.txt](sprint-2-check/c3-r1-cli-claims.txt) |
| US-12 Provider use is counted so the free-tier limits are respected | PASS | Caps per search, per day and in total, kept across a restart; live: "serper 2 / 2 / 60" | [c1-us-12.txt](sprint-2-check/c1-us-12.txt), [c3-r1-cli-claims.txt](sprint-2-check/c3-r1-cli-claims.txt) |
| **US-13** Each thread is scored on upvotes, comment count, age and subreddit relevance, and low scorers are dropped | PASS | The formula and the 0.25 threshold; live R1 kept 20 threads and dropped 5 (scores 0.230 and below). If no thread passes, the best 5 are kept, as a fallback | [c1-us-13.txt](sprint-2-check/c1-us-13.txt), [c3-r1-cli-claims.txt](sprint-2-check/c3-r1-cli-claims.txt) |
| US-13 Comment trees are read three levels deep; each comment is scored on upvotes, length and whether it names a specific model | PASS | Depth 3 asked for, depth 4 ignored; the comment formula; naming a model scores higher | [c1-us-13.txt](sprint-2-check/c1-us-13.txt) |
| US-13 Distinct recommenders per product are counted with an Aho-Corasick matcher over names and aliases, with no LLM; one user posting ten times counts once | PASS | Ten posts count once; the matcher agrees with a slow check on 400 random texts; the module imports no model client. Live R1 printed counts for 19 names | [c1-us-13.txt](sprint-2-check/c1-us-13.txt), [c3-r1-cli-claims.txt](sprint-2-check/c3-r1-cli-claims.txt) |
| US-13 Matching respects word boundaries and prefers the longest name, so "1000XM5" inside "WF-1000XM5" is not a separate product | PASS | The exact example, and the longest-name tests | [c1-us-13.txt](sprint-2-check/c1-us-13.txt) |
| US-13 Threads lose weight with age, with a half-life of 180 days | PASS | Weight 1, 0.5 and 0.25 at 0, 180 and 360 days (it applies to the thread's score) | [c1-us-13.txt](sprint-2-check/c1-us-13.txt) |
| US-13 The same threads always give the same counts | PASS | 5 shuffles give the same counts; scores don't depend on order or on today's date | [c1-us-13.txt](sprint-2-check/c1-us-13.txt) |
| **US-14** The LLM reads each source separately and returns products mentioned, claims (criterion, value or 0 to 10 score, exact quote) and opinions | PASS | One call per source, holding only that source; quotes not in the source are dropped. An opinion is a claim with a score | [c1-us-14.txt](sprint-2-check/c1-us-14.txt) |
| US-14 Invalid output is retried once and then discarded, never repaired by guessing | PASS | Retried once on each model of the route, then the source is dropped; broken JSON is refused, never repaired | [c1-us-14.txt](sprint-2-check/c1-us-14.txt) |
| US-14 Every claim stores its source URL, quote and time | PASS | URL and times come from the source; a claim without them is invalid | [c1-us-14.txt](sprint-2-check/c1-us-14.txt) |
| US-14 [DR-05] Community claims are typed experience; manufacturer and lab claims are typed spec | PASS | Reddit gives experience; rtings and notebookcheck (lab) and dell.com (maker) give spec; the model can't set the type | [c1-us-14.txt](sprint-2-check/c1-us-14.txt) |
| US-14 Product types such as "memory foam" are kept apart from buyable products | PASS | Tests, and live R1 printed "laptop (kind of product)" | [c1-us-14.txt](sprint-2-check/c1-us-14.txt), [c3-r1-cli-claims.txt](sprint-2-check/c3-r1-cli-claims.txt) |
| US-14 A bad source never stops the run | PASS | A source that raises or times out leaves the others; live R5 lost one source and finished | [c1-us-14.txt](sprint-2-check/c1-us-14.txt), [c4a-rate-limit.txt](sprint-2-check/c4a-rate-limit.txt) |
| **US-27** Source fetches run in a thread pool with a fixed size | PASS | 9 tasks on a pool of 3 never run more than 3 at once; one shared pool of 6 | [c1-us-27.txt](sprint-2-check/c1-us-27.txt) |
| US-27 Total fetch time is close to the slowest source | PASS | 1, 2 and 3 s sources finish in under 3.5 s (5 s through the whole stage); live R1: slowest 2.6 s, total 2.6 s | [c1-us-27.txt](sprint-2-check/c1-us-27.txt), [c3-r1-cli-claims.txt](sprint-2-check/c3-r1-cli-claims.txt) |
| US-27 Each API's rate limit is respected | PASS | Serper, both archives, each review site, Mistral and Groq pacing | [c1-us-27.txt](sprint-2-check/c1-us-27.txt) |
| **US-28** Web, Reddit and video text is passed to the LLM as labelled data, with an instruction to ignore any commands inside it | PASS | The text sits inside a labelled block that a page cannot close; retrieved text appears only there. No video text reaches the model in Sprint 2 (YouTube links arrive as web results) | [c1-us-28.txt](sprint-2-check/c1-us-28.txt) |
| US-28 Extraction output is accepted only as schema JSON | PASS | Extraction is held to the strict rule; the schema allows no other fields | [c1-us-28.txt](sprint-2-check/c1-us-28.txt) |
| US-28 HTML is stripped and text length is capped | PASS | Tags, scripts and hidden elements removed; a 3,000-token cap from config on every source | [c1-us-28.txt](sprint-2-check/c1-us-28.txt) |
| US-28 10 test pages with hidden instructions such as "rank product X first" do not change any result | PASS | Each of the 10 hostile pages gives the same source, claims and ranking as its clean twin, with a model that obeys any order it sees. The test proves it can fail. The decide stage is still a sample, so the ranking compared is the test's own | [c1-us-28.txt](sprint-2-check/c1-us-28.txt) |
| **US-32** Filtering, scoring, ranking, weight mapping and mention counting run in tests with no network | **BLOCKED** (B-2) | Weight mapping, Reddit scoring and mention counting run offline, and any network use fails a test at once. Filtering and ranking are not built yet (`app/decide` is a sample until US-17 and US-18), so they can't be tested | [c1-us-32.txt](sprint-2-check/c1-us-32.txt) |
| US-32 Tests use recorded API responses | PASS | A recorded Groq reply runs through the real intake; recorded Serper replies parse; a recorded 429 acts like a live one | [c1-us-32.txt](sprint-2-check/c1-us-32.txt) |
| US-32 Coverage is at least 70% on core modules and 80% on decide/, and the fast suite runs in under 60 seconds | **FAIL** (F-1) | Coverage passes: 98.34% in total, every module gate passes. The fast suite took **64.1 s** on Python 3.11 (58.4 s on 3.13) | [c2-backend-py311.txt](sprint-2-check/c2-backend-py311.txt), [c2-fast-suite.txt](sprint-2-check/c2-fast-suite.txt) |
| US-32 CI blocks a merge if tests fail | **FAIL** (F-2) | CI runs and reports on every pull request, but `main` has no branch protection and no ruleset (GitHub answers 404 and `[]`), by team decision D-19 | [c6-github.txt](sprint-2-check/c6-github.txt) |
| **US-46** A circuit breaker trips a provider after repeated failures in a short window; rate limits and server errors cool it down for 60 to 120 seconds, and a bad key marks it dead for the run | PASS | 3 failures in 60 s cool it for 90 s; a 429 cools it for 60 to 120 s; a bad key is dead for the run. Live R5: Gemini was skipped while "cooling" | [c1-us-46.txt](sprint-2-check/c1-us-46.txt), [c4a-rate-limit.txt](sprint-2-check/c4a-rate-limit.txt) |
| US-46 Bulk extraction rotates across providers in a round-robin pool | **FAIL** (F-3) | The pool rotates across three Groq models, which is one provider. Public bulk work keeps a fixed order: Gemini, Mistral, OpenRouter | [c1-us-46.txt](sprint-2-check/c1-us-46.txt) |
| US-46 Each provider has a daily token counter, and calls stop before the budget is exceeded | **FAIL** (F-4) | Mistral and each Groq pool model have one and stop in time. Gemini and OpenRouter have none (`test_a_provider_without_a_budget_is_never_stopped`) | [c1-us-46.txt](sprint-2-check/c1-us-46.txt) |
| US-46 Tests simulate a failing, a slow and a rate-limited provider | PASS | One test for each, and a fault test where every provider is cooling or dead | [c1-us-46.txt](sprint-2-check/c1-us-46.txt) |
| **US-48** About 20 malformed outputs per parser - intent, criteria, interview reply, rubric, extraction, enrichment and explanation | **BLOCKED** (B-3) | Intent 50, criteria 38, extraction 68 (and Reddit archives 22). Interview reply, rubric, enrichment and explanation have no parser that reads a model's output yet (`tests/golden/README.md`), so they have no cases | [c1-us-48.txt](sprint-2-check/c1-us-48.txt) |
| US-48 Cases cover fenced JSON, missing keys, wrong types, out-of-range values, trailing commas, cut-off text, plain prose, extra invented fields and embedded instructions | PASS | Every kind is required for each model-output parser, and each label is checked against the case's bytes | [c1-us-48.txt](sprint-2-check/c1-us-48.txt) |
| US-48 Every case produces the canonical shape or a clean fallback, and none crashes | PASS | 178 cases give their expected outcome; none raised | [c1-us-48.txt](sprint-2-check/c1-us-48.txt) |
| US-48 New parsers must add their cases before merging | **FAIL** (F-2) | A test finds any new parsing module or model call without at least 15 cases, and fails CI. But with `main` unprotected, a red CI does not stop a merge, so nothing makes the cases come first | [c1-us-48.txt](sprint-2-check/c1-us-48.txt), [c6-github.txt](sprint-2-check/c6-github.txt) |

| Task | Done-line | Result | Proved by |
|---|---|---|---|
| #27 | Progress moves stage by stage in a real search | PASS | Live R2: the run's 16 events, each stage started then done, in order, with source counts from the moment research finished. Live R4: screenshots as the stages changed. Tests: [c1-t27.txt](sprint-2-check/c1-t27.txt). Files: [c3-r2-progress-events.txt](sprint-2-check/c3-r2-progress-events.txt), [c3-stop-run-02.png](sprint-2-check/c3-stop-run-02.png) |
| #28 | Rough and exact answers both reach the API correctly | PASS | API tests: a level is stored by the table, an exact weight as given, both together refused. Frontend adapter tests: each button sends its level, the exact box a number. Live R2 and R3: 10 button answers reached the API. No live run sent an exact value. Files: [c1-t28.txt](sprint-2-check/c1-t28.txt), [c1-frontend-tests.txt](sprint-2-check/c1-frontend-tests.txt) |
| #29 | 300+ labelled pairs; v0 model scored against the fuzzy baseline | Moved to Sprint 3 (D-61) | There is no hard-negative miner, training script or notebook in `ml/`. 275 own pairs are labelled, by machine labellers (D-55 as amended on 10 Nov). [c1-moved-to-sprint-3.txt](sprint-2-check/c1-moved-to-sprint-3.txt) |
| #30, the conflicts entered | Baseline accuracy printed for the conflicts | PASS | `check_conflicts.py`: 15 rows, 5 conflicts, 0 errors. `trust_baseline.py` prints both tables: majority 0 of 5 and tier order 3 of 5; where the known answer's source is not a claim, 0 of 3 and 1 of 3. [c1-moved-to-sprint-3.txt](sprint-2-check/c1-moved-to-sprint-3.txt) |
| #30, past the conflicts entered | Baseline accuracy printed for the 30 conflicts | Moved to Sprint 3 (D-61) | 5 of the 30 are entered (15 were planned for Sprint 2). [c1-moved-to-sprint-3.txt](sprint-2-check/c1-moved-to-sprint-3.txt) |
| L1 | Kappa recorded in ml/DATA.md (the labelling runbook) | Moved to Sprint 3 (D-61) | The tools merged (#58). `labelling_status.py`: 275 distinct pairs in 350 rows; agree-1 four-class kappa 0.933 between Machine A and Machine B. [c1-moved-to-sprint-3.txt](sprint-2-check/c1-moved-to-sprint-3.txt) |
| L2 | Gate on kappa and 150 pairs; scored with 95% intervals | Moved to Sprint 3 (D-61) | Own pairs are not scored yet (`ml/MODELS.md`). [c1-moved-to-sprint-3.txt](sprint-2-check/c1-moved-to-sprint-3.txt) |

### C2. Regression

| Check | Command | Result | File |
|---|---|---|---|
| Backend lint, format, types (3.11) | `ruff check .`, `ruff format --check .`, `mypy app` | PASS: clean, 301 files formatted, no issues in 113 files | [c2-backend-py311.txt](sprint-2-check/c2-backend-py311.txt) |
| Backend tests with coverage (the CI command) | `pytest -q -m "not live" --cov` | PASS: exit 0, 98.34%. 3,862 tests are selected, of which 37 database tests skip without a database | [c2-backend-py311.txt](sprint-2-check/c2-backend-py311.txt), [c2-test-counts.txt](sprint-2-check/c2-test-counts.txt) |
| Coverage gates | `python -m scripts.check_coverage` | PASS: every core module 94.7% or more; `app/decide` 100% | [c2-backend-py311.txt](sprint-2-check/c2-backend-py311.txt) |
| Contract export | `python -m scripts.export_contracts && git diff --exit-code -- ../shared/contracts` | PASS: no drift, on 3.11 and on 3.13 | [c2-backend-py311.txt](sprint-2-check/c2-backend-py311.txt) |
| Windows job (3.13) | `pytest -q -m "not live"` and the contract export | PASS | [c2-backend-py313.txt](sprint-2-check/c2-backend-py313.txt) |
| Database tests | `pytest -q -m db` on a throwaway `postgres:17` | **BLOCKED** (B-1): Docker was not running. CI's backend job runs them with `REQUIRE_DB_TESTS=1`, and it passed on `b6ec081` | [c2-db-tests.txt](sprint-2-check/c2-db-tests.txt) |
| Fast suite under 60 s | `pytest` (repo default: not slow, not live), timed by JUnit | **FAIL** (F-1): 3,846 tests, 0 failures, **64.06 s** on 3.11; 58.35 s on 3.13 | [c2-fast-suite.txt](sprint-2-check/c2-fast-suite.txt) |
| Frontend install | `npm ci` | PASS | [c2-frontend.txt](sprint-2-check/c2-frontend.txt) |
| Frontend lint | `npm run lint` | PASS | [c2-frontend.txt](sprint-2-check/c2-frontend.txt) |
| Frontend types | `npm run typecheck` | PASS | [c2-frontend.txt](sprint-2-check/c2-frontend.txt) |
| Frontend unit tests | `npm test` | PASS: 20 files, 521 tests | [c2-frontend.txt](sprint-2-check/c2-frontend.txt) |
| Frontend build | `npm run build` | PASS | [c2-frontend.txt](sprint-2-check/c2-frontend.txt) |
| Frontend end-to-end, mock | `npm run test:e2e` | **FAIL** (F-5): 165 passed, 2 skipped, 1 failed: `motion.spec.ts` "typing is never dropped and stays smooth", frame rate 28 against at least 30 | [c2-frontend.txt](sprint-2-check/c2-frontend.txt) |
| ml job | ruff, format, `mypy --strict`, pytest, `check_labels`, `kappa --min 0.8`, the size guard | PASS: 648 tests, kappa 0.859, 66 tracked files of 1 MiB or less | [c2-ml.txt](sprint-2-check/c2-ml.txt) |
| The CLI | `python -m app.cli "Laptop for coding under 80k" --claims` (R1) | PASS: understood, criteria, rubric, results with a coverage note; "Saved: yes" | [c3-r1-cli-claims.txt](sprint-2-check/c3-r1-cli-claims.txt) |
| Live end-to-end, database on | `scripts/dev.py`, then `LIVE_E2E=1 npx playwright test -c e2e/live/playwright.live.config.ts` (R2) | PASS: 1 passed in 22.1 s; health `"database":"ok"`; `check_request_saved.py` says "saved" | [c2-live-e2e.txt](sprint-2-check/c2-live-e2e.txt) |
| Live end-to-end, database off | the same with `scripts/dev.py --no-db` (R3) | PASS: 1 passed in 19.5 s; health `"database":"off"`; "not found"; the result says `saved: false` | [c2-live-e2e.txt](sprint-2-check/c2-live-e2e.txt) |

### C3. The demo, live

| Check | Result | Evidence | File |
|---|---|---|---|
| A search that uses Reddit, web search and review sites | PASS | R1: 47 sources: 20 Reddit threads, 20 web results, 7 review pages. Reddit came from the 24-hour cache (fetched 9 Nov, 12:08 UTC) | [c3-r1-cli-claims.txt](sprint-2-check/c3-r1-cli-claims.txt) |
| Extracted claims with quotes | PASS | R1: 7 claims from Reddit threads, each with a quote (shown as a marker in the evidence, by D-53). R5 and R6: review-site claims with their quotes, such as `HP 14-ep1152TU (C72QJPA) Laptop \| battery_life \| 8.5 Hrs \| "8.5 Hrs Battery Life"` | [c3-r1-cli-claims.txt](sprint-2-check/c3-r1-cli-claims.txt), [c4a-rate-limit.txt](sprint-2-check/c4a-rate-limit.txt), [c4b-reddit-off.txt](sprint-2-check/c4b-reddit-off.txt) |
| Exact recommender counts | PASS | R1: "Recommenders per product", such as "thinkpad: 4 people in 4 threads", 19 names in all, counted by code | [c3-r1-cli-claims.txt](sprint-2-check/c3-r1-cli-claims.txt) |
| P4's hostile pages change nothing | **BLOCKED** (B-4) | Not shown live: nothing serves a hostile page to a real search, and building that would change the app. What exists: the 10 hostile pages, each against its clean twin, through the real wrapper and parsers with a model that obeys any order. All 10 give identical results (US-28) | [c1-us-28.txt](sprint-2-check/c1-us-28.txt) |
| The research screen moving stage by stage | PASS | R4a and R4b screenshots, from "Finding reviews" running, to done with "Found 47 sources", to "Reading what each source says" running. The R2 and R4a event streams show every stage in order | [c3-stage-01.png](sprint-2-check/c3-stage-01.png), [c3-stage-02.png](sprint-2-check/c3-stage-02.png), [c3-stop-run-01.png](sprint-2-check/c3-stop-run-01.png), [c3-stop-run-02.png](sprint-2-check/c3-stop-run-02.png), [c3-r4a-progress-events.txt](sprint-2-check/c3-r4a-progress-events.txt) |
| A stopped search | PASS | R4b: Stop research, the "Stop research?" dialog, then "Search stopped". The backend took `DELETE` with 202; the run ended `cancelled` after the extract stage, with no result | [c3-stop-dialog.png](sprint-2-check/c3-stop-dialog.png), [c3-stopped.png](sprint-2-check/c3-stopped.png), [c3-r4b-stop.txt](sprint-2-check/c3-r4b-stop.txt), [c3-r4-walk.txt](sprint-2-check/c3-r4-walk.txt) |

[c3-stage-04.png](sprint-2-check/c3-stage-04.png) is R4a's finished results page. It shows the "isn't saved" banner and
the coverage note, with sample products only.

### C4. Drills

| Drill | Command | Result | File |
|---|---|---|---|
| (a) Rate-limit one provider | R5: `python -m app.cli "Laptop for coding under 80k" --claims --no-db`, with `REDDIT_SOURCE=off`, `EXTRACT_MAX_SOURCES=3`, and Gemini answered by a fake 429 | **FAIL** (F-6). The search moved on and arrived with no error: Gemini got 2 429s, was then skipped as "cooling", and Mistral read the sources. But one source's chain also met a real Mistral 429, then an OpenRouter call that failed after 27.6 s (R5's call log, at the end of the file). That passed the 15 s limit per source, so the shopper saw "Extract took too long, so a simpler fallback was used." The sprint plan says the switch happens without the user noticing | [c4a-rate-limit.txt](sprint-2-check/c4a-rate-limit.txt), [c5-call-log.txt](sprint-2-check/c5-call-log.txt) |
| (b) Reddit off, live | R6: the same with `REDDIT_SOURCE=off` only | PASS: the result arrived; its coverage note says "Reddit could not be read."; 4 review-site claims with quotes | [c4b-reddit-off.txt](sprint-2-check/c4b-reddit-off.txt) |

### C5. Rules

| Check | Result | Evidence | File |
|---|---|---|---|
| Private and Reddit text reached only providers that don't train on inputs | PASS | All 7 private calls (detect_intent) and all 22 Reddit calls (17 sent, 5 held back by our own limits) were on Groq (`trains_on_inputs: false`). Gemini, Mistral and OpenRouter received public page text only. Count of private or Reddit calls to a training provider: **0** | [c5-call-log.txt](sprint-2-check/c5-call-log.txt) |
| No Reddit text on disk | PASS | Every Reddit title, post and comment line in the cache (1,482 lines) was searched for in 1,869 files: the working tree, the evidence and the check's scratch output. Two files matched, but only on two product names (21 and 24 characters) that a Reddit comment consisted of: this check's R1 output (in its recommender list) and a local, git-ignored notes file. A second scan over the final evidence folder found the same two names, in `c3-r1-cli-claims.txt`, and nothing else. A product's name is what the retention rule keeps (P17); Om to confirm that this holds when the name was a comment's whole text | [c5-reddit-text-and-secrets.txt](sprint-2-check/c5-reddit-text-and-secrets.txt) |
| The Reddit cache stays within 24 hours | PASS | 12 files, the oldest 21.5 h | [c5-reddit-text-and-secrets.txt](sprint-2-check/c5-reddit-text-and-secrets.txt) |
| No Reddit text in the `public.searches` rows this check created | PASS | One read-only transaction: both rows (R1, R2) print `holds Reddit text: False` | [c5-reddit-text-and-secrets.txt](sprint-2-check/c5-reddit-text-and-secrets.txt) |
| No recording holds a secret | PASS | Each of the 8 files under `backend/tests/fixtures/recorded/` prints `holds a secret: False`. Each was checked against key-shaped patterns and against the 12 values set among the 15 secret-named variables, compared in memory and never printed | [c5-reddit-text-and-secrets.txt](sprint-2-check/c5-reddit-text-and-secrets.txt) |

### C6. GitHub (read-only)

| Check | Result | Evidence | File |
|---|---|---|---|
| Every Sprint 2 pull request was opened by its owner | **FAIL** (F-7) | All 24 (#50 to #73) were opened and merged by Om's account. Only Om's own 4 (#50, #58, #72, #73) were opened by their owner. D-18 says the owner opens the pull request | [c6-github.txt](sprint-2-check/c6-github.txt) |
| Each merged prompt is one commit authored by its owner | PASS | 24 of 24 have one commit, by the owner the prompt named | [c6-github.txt](sprint-2-check/c6-github.txt) |
| No pull request title or description names an AI tool | **FAIL** (F-8) | PR #50's description has the checklist line naming the root rules file by its old name. That template line was changed by #50 itself. The other 23 are clean | [c6-github.txt](sprint-2-check/c6-github.txt) |
| No commit message names an AI tool | **FAIL** (F-10) | Sprint 2's 48 commits and merge commits: 0. But seven older messages on `main`, from 12 Oct to 7 Nov, do | [c6-github.txt](sprint-2-check/c6-github.txt) |
| No branch name names an AI tool | **FAIL** (F-9) | 2 of 71 branches on GitHub: `docs/…-md-refused-command` and `docs/…-md-v2`, both from Sprint 1 | [c6-github.txt](sprint-2-check/c6-github.txt) |
| No tracked file names an AI tool | PASS | 0 paths and 0 files with the text | [c6-github.txt](sprint-2-check/c6-github.txt) |
| Each Sprint 2 issue is closed by its pull request | PASS | There is nothing to close: the repository has 0 issues, as D-03 intends (no per-task issues) | [c6-github.txt](sprint-2-check/c6-github.txt) |

### C7. Supabase (read-only)

| Check | Result | Evidence | File |
|---|---|---|---|
| Migration history equals `supabase/migrations` | PASS | The same 3 versions and names, in order | [c7-supabase.txt](sprint-2-check/c7-supabase.txt) |
| Row level security on, no policies, no anon or authenticated access, on every public table | PASS | `requests` and `searches`: RLS on, 0 policies, no access for either role, 0 grants | [c7-supabase.txt](sprint-2-check/c7-supabase.txt) |
| The rows this check created | PASS | 4 rows: a request and a search for each of 2 sessions (section 6) | [c7-supabase.txt](sprint-2-check/c7-supabase.txt) |

## 4. Failures

**F-1. The fast suite takes longer than 60 seconds.** C1 and C2, US-32.
- **Expected:** "the fast suite runs in under 60 seconds".
- **What happened:** 3,846 tests passed in 64.06 s on Python 3.11 (58.35 s on 3.13), as pytest's own JUnit timing
  shows.
- **Steps:** in `backend/`, on a fresh Python 3.11 venv: `pytest -p no:cacheprovider --junitxml=fast.xml`.
- **Cause, suspected:** the suite has grown. Early in Sprint 2 the notes record 27 to 37 s (1,834 tests at the time);
  it now has 3,846. A few short commands, about 2 s of work in all, ran on the machine during the 3.11 run. CI does not
  time the suite.
- **Suggested owner:** Harshkumar.

**F-2. CI does not block a merge.** C1, US-32 and US-48.
- **Expected:**
  - US-32: "CI blocks a merge if tests fail".
  - US-48: "New parsers must add their cases before merging".
- **What happened:** CI goes red on a failing test or a parser without cases, but nothing stops the merge.
- **Steps:** `gh api repos/202401121-cmyk/Group13/branches/main/protection` answers 404, and
  `gh api repos/202401121-cmyk/Group13/rulesets` answers `[]`.
- **Cause, confirmed:** team decision D-19 keeps `main` unprotected. US-25 was reworded to match; US-32 and US-48 were
  not.
- **Suggested owner:** Om, to reword both criteria the same way.

**F-3. Rotation is across models, not providers.** C1, US-46.
- **Expected:** "Bulk extraction rotates across providers in a round-robin pool".
- **What happened:** the round-robin pool holds three Groq models. Public bulk work keeps the fixed order Gemini,
  Mistral, OpenRouter.
- **Steps:** read `pool` in `backend/config/models.yaml`, and run
  `pytest tests/unit/llm/test_pool.py -k "rotate or chain" -v`
  (`test_members_rotate_in_order_across_three_members`, `test_public_bulk_work_keeps_its_chain`).
- **Cause, confirmed:** design. Private and Reddit text may only reach Groq (D-51), so a pool across providers could
  not carry it.
- **Suggested owner:** Jhanvi, or Om to reword the criterion to "across models".

**F-4. Not every provider has a daily token counter.** C1, US-46.
- **Expected:** "Each provider has a daily token counter, and calls stop before the budget is exceeded".
- **What happened:** Mistral (1,000,000 a day) and each Groq pool model (200,000 a day) have one. Gemini paces per
  minute only. OpenRouter has no limit, so it is never stopped.
- **Steps:** `pytest tests/unit/llm/test_limits.py::test_a_provider_without_a_budget_is_never_stopped -v`, and read the
  `daily_tokens` entries in `backend/config/models.yaml`.
- **Cause, confirmed:** no `daily_tokens` is set for Gemini or OpenRouter, and a provider without one is never
  stopped.
- **Suggested owner:** Jhanvi.

**F-5. One mock end-to-end test fails on frame rate.** C2.
- **Expected:** every mock end-to-end test passes.
- **What happened:** `e2e/quality/motion.spec.ts:94` "typing is never dropped and stays smooth" measured 28 frames a
  second against at least 30. 165 passed and 2 were skipped.
- **Steps:** in `frontend/`: `npm ci`, then `npm run test:e2e`.
- **Cause, suspected:** the measure depends on the machine.
  - This was a Windows laptop running the dev server.
  - Five pytest collection commands (`c2-test-counts.txt`, 09:14 UTC) ran during part of the e2e run, which lasted
    about 09:08 to 09:17 UTC.
  - CI does not run this suite (`docs/ci-frontend-e2e-proposal.md`).
- **Suggested owner:** Manu.

**F-6. In the rate-limit drill, the shopper could tell.** C4a, US-46 and US-14.
- **Expected:** a rate-limited provider is passed over without the shopper noticing.
- **What happened:** after Gemini's 429s, the search moved on and finished with no error. But the coverage note said
  "Extract took too long, so a simpler fallback was used."
- **Steps:** run R5 as in C4, with any way of making Gemini answer 429.
- **Cause, confirmed by R5's call log** (the end of [c4a-rate-limit.txt](sprint-2-check/c4a-rate-limit.txt)). For one
  source the chain went:
  1. Gemini, the fake 429, at 15:03:11 local time;
  2. Mistral, a real 429, at 15:03:12;
  3. OpenRouter's free model, which failed at 15:03:40, after 27.6 s.

  That passed the extract stage's 15 s limit per source (`STAGE_TIME_LIMITS_S`), so the fallback ran. The CLI printed
  the result at 18.5 s; the process ended once the OpenRouter call returned.
- **Suggested owner:** Jhanvi (the fallback order and the per-source time budget).

**F-7. Pull requests were not opened by their owners.** C6.
- **Expected:** D-18: "the owner opens the pull request and Om merges".
- **What happened:** all 24 Sprint 2 pull requests were opened by Om's account. Each commit is still by its owner.
- **Steps:** `gh pr view <n> --json author,commits` for n = 50 to 73.
- **Cause, confirmed:** the owners pushed their branches, and Om opened every pull request.
- **Suggested owner:** Om and each owner. From Sprint 3, each owner opens their own pull request, or D-18 is changed to
  match practice.

**F-8. PR #50's description names an AI tool.** C6, D-10.
- **Expected:** no pull request title or description names an AI tool.
- **What happened:** a checklist line in the description gives the root rules file by its old name.
- **Steps:** `gh pr view 50 --json body`, then search it with the kit's name list.
- **Cause, confirmed:** the description was written from the pull request template as it was before #50 changed that
  line.
- **Suggested owner:** Om, to edit the description on GitHub.

**F-9. Two Sprint 1 branch names name an AI tool.** C6, D-10.
- **Expected:** no branch name names an AI tool.
- **What happened:** `docs/…-md-refused-command` and `docs/…-md-v2` are still on GitHub. Both are merged (#48, #26).
- **Steps:** `git fetch --prune`, then `git branch -r` searched with the kit's name list.
- **Cause, confirmed:** the branches were named before D-10 was applied to branch names, and were never deleted.
- **Suggested owner:** Om, to delete both remote branches.

**F-10. Seven commit messages on `main` name an AI tool.** C6, D-10.
- **Expected:** no commit message names an AI tool.
- **What happened:** seven messages from 12 Oct to 7 Nov do, including the two merge messages of the branches in F-9.
  Sprint 2's own 48 have none.
- **Steps:** `git log origin/main --format='%h %s'`, searched with the kit's name list.
- **Cause, confirmed:** they were written before D-10.
- **Suggested owner:** Om. Changing them means rewriting `main`, which the team never does, so record them as a known
  exception.

**B-1. Database tests not run here.** C2.
- **Why it could not run:** Docker Desktop was not running, and starting it from this session was refused.
- **What exists:** CI ran them on `b6ec081` against `postgres:17` with `REQUIRE_DB_TESTS=1`, and they passed
  ([c2-ci-on-main.txt](sprint-2-check/c2-ci-on-main.txt)).
- **Steps:** start Docker, then run once, from `backend/`: `docker run -d --rm --name ss-test-db -e
  POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:17`, and `TEST_DATABASE_URL=… REQUIRE_DB_TESTS=1 pytest -q -m db`.
- **Suggested owner:** Om to start Docker, then Palak.

**B-2. Filtering and ranking.** C1, US-32.
- **Why it could not run:** `app/decide` is a sample stage until US-17 and US-18 build filtering and ranking.
- **Steps:** after those merge, run their tests with the network guard on, as `pytest` does by default.
- **Suggested owner:** the US-17 and US-18 owners.

**B-3. Four parsers.** C1, US-48.
- **Why it could not run:** interview reply, rubric, enrichment and explanation don't read a model's output yet
  (`tests/golden/README.md`). They arrive with US-39, US-41 and US-20.
- **Steps:** `pytest tests/golden -v` after each merges. The registry test then requires at least 15 cases for each.
- **Suggested owner:** the US-20, US-39 and US-41 owners.

**B-4. Hostile pages in a live search.** C3.
- **Why it could not run:** no way exists to put a hostile page into a real search without changing the app.
- **What exists:** the fault test, with a model that obeys any order (US-28 in C1).
- **Steps:** a test-only research source that serves `tests/fixtures/hostile/`, enabled by a setting, then one live
  run.
- **Suggested owner:** Pal (US-28).

## 5. Live-call ledger

The caps were LLM 40, Serper 15 and Reddit archive 30.

**Totals: LLM 32, Serper 2, Reddit archive 0. Also 14 review-page fetches; 2 Gemini calls answered by the drill's fake
429, never sent; 0 calls refused by a cap.**

| Run | LLM calls sent | Serper | Reddit archive | Review pages |
|---|---|---|---|---|
| R1: CLI with claims, database on, 6 sources | 6 (Groq 6) | 2 | 0 | 2 |
| R2: live end-to-end, database on | 4 (Groq 4) | 0 | 0 | 2 |
| R3 and R4a: live end-to-end, database off; the first screen walk | 8 (Groq 8) | 0 | 0 | 4 |
| R4b: screen walk, then Stop | 4 (Groq 4) | 0 | 0 | 2 |
| R5: rate-limit drill | 5 (Groq 1, Mistral 3, OpenRouter 1) | 0 | 0 | 2 |
| R6: Reddit-off drill | 5 (Gemini 3, Groq 1, Mistral 1) | 0 | 0 | 2 |

By task and data class (from the call log):
- `detect_intent`, private: Groq 7.
- `extract_claims`, Reddit: Groq 17 sent, plus 5 that our own limits held back.
- `extract_claims`, public: Gemini 3 sent and 2 faked; Mistral 4; OpenRouter 1.

Per-run files: [live-call-ledger.txt](sprint-2-check/live-call-ledger.txt) and
[c5-call-log.txt](sprint-2-check/c5-call-log.txt).

## 6. Rows this check created in Supabase

Read back with one read-only `select` of `session_id` and `created_at`.

| Table | session_id | created_at (UTC) | Run |
|---|---|---|---|
| requests | `sess_736269c49abf68a3400dc02e` | 2026-11-10 09:19:40 | R1 |
| searches | `sess_736269c49abf68a3400dc02e` | 2026-11-10 09:19:48 | R1 |
| requests | `sess_ae81291919a74bd5cea0b54f` | 2026-11-10 09:21:29 | R2 |
| searches | `sess_ae81291919a74bd5cea0b54f` | 2026-11-10 09:21:40 | R2 |

Some runs ran with the database off and saved nothing:
- R3: `sess_b1782e8d3c845337fde40710`
- R4b: `sess_c8ce6802cd41b27d792c0a4e`
- R4a, R5 and R6: their IDs were not printed with the database off.

These statements are for Om to review and run. They were not run here.

```sql
delete from public.searches where session_id in ('sess_736269c49abf68a3400dc02e', 'sess_ae81291919a74bd5cea0b54f');
delete from public.requests where session_id in ('sess_736269c49abf68a3400dc02e', 'sess_ae81291919a74bd5cea0b54f');
```

## 7. Observations and gaps (not counted)

| # | What | Owner |
|---|---|---|
| G-1 | D-61 is not written in the decision register | Om |
| G-2 | No live run sends an exact value through the interview. The live test clicks rough buttons only | Manu |
| G-3 | Live counts treat parts as products: "1TB SSD", "16gb DDR5", "32gb DDR5" and "5070 TI" got recommender counts | Vaishvi |
| G-4 | In R1, a source skipped for want of time ("no room before the time limit") was reported as "the AI services were busy" | Jhanvi |
| G-5 | In R5, a battery capacity ("53.8 WHrs") was read as battery life | Jhanvi |
| G-6 | With 6 sources allowed, all 6 went to Reddit threads, so no web or review page was read in R1 | Jhanvi |
| G-7 | No live run sent a comparison query, because the match stage is still a sample ("fewer than two candidates") | Jenish |

## 8. Blocked items, 10 Nov

Three of the four were run after the check (B-1, B-3 and B-4), by Palak Patel, on `main` at `483d8f2`, as the Sprint 2
Fix Pack asked; B-2 has nothing to run. Sections 1 to 7 stay as the check found them; this section records how each
blocked item came out.

| Item | Result | What was run | Evidence |
|---|---|---|---|
| B-1, the database tests | PASS | CI's backend command (`pytest -q -m "not live" --cov --cov-report=term-missing`, with `TEST_DATABASE_URL` and `REQUIRE_DB_TESTS=1` set only on the command, plus `--junitxml`, which only writes a report for the counts) against a throwaway `postgres:17` (17.11) in Docker, removed after. 3,862 passed, 0 failed, 0 skipped; all 37 `db` tests passed; coverage 98.45% | [b1-db-tests.txt](sprint-2-check/b1-db-tests.txt) |
| B-2, filtering and ranking | Moved (D-61) | Nothing to run: US-17 and US-18 are not built yet. It is checked in Sprint 3's review check (#46) | — |
| B-3, a new parser without cases | PASS | A scratch `backend/app/intake/scratch_parsing.py` with one model call. `test_every_parsing_module_is_registered` and `test_every_llm_call_has_a_registered_parser` both fail, naming the module and the call (2 failed, 30 passed). With the module deleted: 32 passed, and the working tree is clean | [b3-registry.txt](sprint-2-check/b3-registry.txt) |
| B-4, P4's hostile pages | PASS | Offline, as the Fix Pack asked: `pytest tests/faults/test_hostile_pages.py -v -o addopts=--strict-markers`, 33 passed. Each of the 10 hostile pages gives the same result as its clean twin | [b4-hostile-pages.txt](sprint-2-check/b4-hostile-pages.txt) |

- **B-1's first attempt** pointed at `localhost:5432`. On this laptop that is a local PostgreSQL 18 service, not the
  container, so all 37 `db` tests ended in "password authentication failed" (the other 3,825 passed). The service was
  left alone. The container was moved to host port 5433 and checked to answer as Postgres 17.11, and the command was
  run once more; that run is the result above. Both are in the evidence file. Like the screen walk in section 2, this
  is a second run after a fault in the set-up, not in the app: the first attempt never reached the container.
- **B-3's module** never left the working tree. It was deleted before anything was committed. The four parsers that
  US-48 names will each need 15 cases or more when they arrive.
- **B-4 stays offline.** A hostile page in a live search still needs the test-only research source in section 4's B-4
  steps.
