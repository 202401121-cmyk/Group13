# Sprint 1 review check

Checked by Palak Patel (QA) on 4 November 2026, against `main` at **`99f92c1`** (Merge pull request #32). Nothing in the
code was changed for this check. Every result below comes from a command run on that commit, from CI, or from
the service named in the row; evidence files are in [sprint-1/](sprint-1/).

**Summary: 12 of 21 rows pass.**
- 4 rows fail on the code; three small fixes cover them (see "Fixes").
- 1 row needs a design decision: the home page's speed.
- 4 rows wait on people: the survey and interviews, the poster, the write-up, and pull-request authorship.

## How it was checked

- **Checkout:** a clean checkout of `main` at `99f92c1`, a new Python 3.11 virtual environment, and `npm ci` in
  `frontend/`. Windows 11, Git Bash, Python 3.11.15, Node 22.18. The root `.env` holds the real keys.
- **CI commands, run locally** ([ci-commands.txt](sprint-1/ci-commands.txt)):
  - actionlint;
  - backend ruff, ruff format and mypy;
  - backend pytest with a throwaway `postgres:17` and `REQUIRE_DB_TESTS=1`;
  - the contract and fault tests;
  - the contract export;
  - the ML lint, types, tests and scripts;
  - the frontend lint, typecheck, tests, build and `test:e2e`.
- **CI on GitHub:** the run for `99f92c1` passed all five jobs (backend, backend-windows, ml, secrets, frontend). The
  backend job started a `postgres:17` service, and with `REQUIRE_DB_TESTS=1` the database tests cannot skip.
- **`scripts/doctor.py`** ([doctor.txt](sprint-1/doctor.txt)).
- **The live end-to-end test**, run six times: database on, database off, and four drills
  ([live-runs-and-drills.txt](sprint-1/live-runs-and-drills.txt)).
- **The CLI**, with keys on and off ([cli-detection-and-criteria.txt](sprint-1/cli-detection-and-criteria.txt),
  [cli-drills.txt](sprint-1/cli-drills.txt)).
- **The interview through the API**, with every turn timed ([interview-api-timing.txt](sprint-1/interview-api-timing.txt)).
- **TruffleHog** over the full history ([secret-scan.txt](sprint-1/secret-scan.txt)).
- **The Supabase Security Advisor**, read through the project's dashboard API.

## Exit criteria

| # | Row | How checked | Evidence | Result | Owner | One-line fix |
|---|---|---|---|---|---|---|
| 1 | The CLI and the browser both run a request through every stage and show a ranked list with sources | CLI search; live browser test in live mode; screenshots at 390 and 1440 px | The CLI ranks 3 products, each with its sources. The live test passed 6 times. [results-390.png](sprint-1/results-390.png), [results-1440.png](sprint-1/results-1440.png). Results are the Sprint 1 placeholder data, as planned | PASS | | |
| 2 | Every stage has a contract and a contract test, and swapping any stage for a failing one triggers its fallback | `pytest tests/contract`, `pytest tests/faults`, and a read of the fault tests | 95 contract tests and 20 fault tests pass. Failing swaps are tested only for intake, criteria, interview and research, plus a slow verify. Extract, match, enrich, decide, explain and commerce have no failing-stage test | PARTIAL | Pal Jalodara | Add one fault test that fails each search stage in turn and checks its fallback |
| 3 | Detection shows category, use, budget, region and rules; "watch" asks a question; with every LLM off, keywords still find category and budget | CLI with keys on and off | "Gaming laptop under 80k": Laptop, use gaming, up to ₹80,000, IN, by LLM. Earbuds: 1 must-have found. "watch": "Which kind of watch do you mean?" With every key off: Laptop, ₹80,000, by rules; "watch" still asks | PASS | | |
| 4 | Criteria with definitions appear for three categories, including one never seen, and the fallback set appears when generation fails | CLI for a laptop, earbuds and a telescope (not in the lexicon); the criteria stage run with logging | Every set has definitions, and the fallback set appears when generation fails. But **generation fails every time a use case is given**: Groq's model spends all 1,024 output tokens and the reply is cut off (`out=1024`, `invalid_output` twice), and Cerebras answers 402. So a never-seen category only gets the generic set ("Effectiveness", "Reliability" …). Without a use case, generation works | PARTIAL | Jhanvi Barot (US-03) | Give the criteria call a larger output limit |
| 5 | The interview runs at least 5 questions and stores the answers | API run; live test | 8 questions; 8 answers stored in the session's history; the rubric is finished. The live runs answered 8 or 9 | PASS | | |
| 6 | Switching off one LLM provider key does not stop the app | Drills below | Groq off and Mistral off: the CLI and the browser flow both finish | PASS | | |
| 7 | CI runs lint, types and tests on every pull request; no secret in the repo; every member has a merged pull request under their own name; branch protection | CI runs on PRs #26 to #32; TruffleHog; `gh pr list`; commit authors on `main` checked against GitHub accounts; `gh api` for protection | Five jobs on every PR, all green. TruffleHog 3.99.8: 0 verified, 0 unverified. All 10 members have commits on `main` linked to their own accounts, but **every pull request was opened by Om-5640**. Branch protection: the API answers 404 to our account, so it could not be read | PARTIAL | Om Pandya | Confirm with the course whether commits under each member's name count. If they don't, each member opens one PR. Add a screenshot of the branch protection settings |
| 8 | The survey has 30+ responses, and the interviews, observation, expert interview and API limits are written up | `docs/`, and Slack | API limits are written up ([api-setup.md](../api-setup.md)). The survey form was shared in `#all-shoppingresearchagentdau` on 19 Sep 2026, but no response count is recorded. No interview, observation or expert write-up is in the repo, and there is no `#elicitation` channel | FAIL (waiting on people) | Jhanvi; Om, Rushiraj, Jenish; Harshkumar; Pal | Record the survey count; commit the interview, observation and expert notes |
| 9 | The poster is printed | Owner's confirmation | Not received | WAITING | Vaishvi, Palak | A photo or confirmation |

## Task done-lines

| Task | Row | How checked | Evidence | Result | Owner | One-line fix |
|---|---|---|---|---|---|---|
| #1 | The CLI prints a ranked list from stub stages; every stage has a contract test | CLI; contract tests | 3 ranked products with sources, a filtered one and a "can't tell" one; 95 contract tests pass | PASS | | |
| #2 | CI green on Linux and Windows; the rupee sign prints correctly; no secret in the repo | CI run for `99f92c1`; CLI in Git Bash with no encoding setting; TruffleHog | backend and backend-windows green; "up to ₹70,000" prints; 0 secrets | PASS | | |
| #3 | With one provider key removed, a call still succeeds; tests use fake providers | Drills; `tests/conftest.py` | Groq off: intake falls back to rules and the criteria are generated by another provider. Every test uses `FakeLLMClient` | PASS | | |
| #4 | "Gaming laptop under 80k" parses; "watch" asks which kind; with every LLM off, keywords still detect category and budget | CLI | As in exit criterion 3 | PASS | | |
| #5 | Three categories, one never seen, return criteria; the fallback set appears when generation fails | As in exit criterion 4 | As in exit criterion 4 | PARTIAL | Jhanvi Barot (US-03) | As in exit criterion 4 |
| #6 | 5+ questions run through the API and answers are stored; each turn under 5 s | 10 replies sent through the API; each turn timed | Laptop has 8 criteria, so 8 turns ran; the slowest took 31 ms. 8 answers stored | PASS | | |
| #7–#11 (F-import, F4) | The app builds in CI; the request screen gives the right follow-up for empty, unclear and product-less requests; a full interview runs in the browser and stores answers; the results page renders; the F4 report shows no open severity 3 or 4 issue | CI; the request screen's tests; the live test; [the F4 report](../frontend-quality-report.md) | Builds in CI. The request tests cover empty, ambiguous and examples-instead-of-guessing. The interview and results run live. **The F4 report still has one severity-3 issue open (O9)**: Lighthouse mobile Performance is 47, 84 and 83, against a target of 90 | FAIL | Manu Pardhan, with Yashkumar Patel | O9: decide on the home page's typing effect and load the fonts without blocking. A design change, so Om may move it to Sprint 2 |
| #12 | The kappa script runs on the 50-pair sample; the raw data folder is ignored | `kappa.py` on the two example labellers; `git check-ignore` | Kappa 0.859, at least 0.8. `ml/data/raw/` is ignored, with 0 files tracked | PASS | | |
| #13 | Baseline numbers are printed for the labelled pairs (public datasets) | `ml/results/matching_baseline.json` and `ml/MODELS.md` | The results file from the 26 Oct baseline run is committed. It was not re-run here, because the public datasets are not downloaded on this laptop (`match_baseline.py` exits 2, as documented) | PASS | | |
| #14 | Each request is saved with a session ID; with the database down, a search still finishes; the Security Advisor shows only "RLS enabled, no policy" | `check_request_saved.py`; the database-down drill; the Advisor | Run 1: "saved". Database down: health "down" and the flow passed. Advisor (4 Nov, 15:55 UTC): only "RLS Enabled No Policy" (INFO) on `public.requests`. 5 test rows remain in the table and are for Vaishvi to delete | PASS | | |
| Set-up | `scripts/doctor.py` passes on a fresh clone, and `docs/setup.md` alone is enough to run the whole stack | Doctor before and after the CI commands; the set-up guide followed for every run | After `npm ci` the doctor shows no FAIL. **After the CI contract export it fails with "Frontend contract types: 56 of 56 older than their schema"**: the export rewrites every file even when nothing changed, so the timestamps move. Its own fix line clears it | PARTIAL | Palak Patel (US-25) | Make the contract export leave unchanged files alone |
| #15 | Elicitation findings are committed and posted | `docs/`, and Slack | Not in the repository | WAITING | Harshkumar Kodavala | Commit the write-up after the survey and interviews |

## Drills

| Drill | Command | Time | Outcome |
|---|---|---|---|
| One LLM key off: Groq (first in the private chain) | `GROQ_API_KEY= python scripts/dev.py --no-db …`, then the live test; the CLI the same way | Live test 12 s; CLI 7 s | Finished. Intake used its keyword rules, and the criteria were generated by a public-text provider |
| A second key off: Mistral (first in the public chain) | `MISTRAL_API_KEY= …` | Live test 20 s; CLI 10 s | Finished |
| Every LLM key off | All five keys blank | Live test 12 s; CLI 1 s | Finished. Keywords found the category and budget, criteria came from the fixed set, and the results showed. No crash |
| Database down | `DATABASE_URL` at `127.0.0.1:9` | Live test 21 s; CLI 11 s | Health "down"; the flow finished; the CLI said the request could not be saved and carried on |

## Fixes

| Row | Owner | Fix | Kind |
|---|---|---|---|
| Exit 4, #5 | Jhanvi Barot | A larger output limit for the criteria call | Fix (W7) |
| Exit 2 | Pal Jalodara | A fault test that fails each search stage | Fix (W7) |
| Set-up | Palak Patel | The contract export skips unchanged files | Fix (W7) |
| #7–#11 (O9) | Manu Pardhan, Yashkumar Patel | Home page speed | Design change: Om to decide, or Sprint 2 |
| Exit 7 | Om Pandya | Course rule on PR authorship; branch protection screenshot | People |
| Exit 8, #15 | Jhanvi, Om, Rushiraj, Jenish, Harshkumar, Pal | Survey count and write-ups | People |
| Exit 9 | Vaishvi, Palak | Poster | People |

After the fixes land, only these rows are checked again, in a new commit.
