# Drills: break it live, check what the shopper sees, put it back (US-50)

The app must work in the worst case, not just the demo case. These drills let anyone break one thing on their own
machine, run a search, and check the result against what the plan promises. Every automated test for the same failures
is in the coverage table at the end.

## Before you start

- Run the stack with `python scripts/dev.py` from the repository root (`--no-db` for the database off). Stop it with
  Ctrl+C. Every switch below goes **on the command line, in front of that command**. A variable set there wins over the
  `.env` file, so the `.env` file is never edited, and stopping the stack puts everything back.
- **Never print a key.** Each switch sets a made-up value (`made-up-not-a-key`) or turns a part off. Don't open `.env` on
  a shared screen, and don't `echo` a key to check it.
- Use a made-up request, such as `Laptop for coding under 80k`.
- PowerShell sets a variable for one command like this: `$env:NLI="off"; python scripts/dev.py`, then
  `Remove-Item Env:NLI` afterwards. Git Bash: `NLI=off python scripts/dev.py`.

## 1. A key: one AI provider down

- **The switch:** `GEMINI_API_KEY=made-up-not-a-key python scripts/dev.py --no-db`
- **What to look for:**
  - The search finishes, perhaps a little slower, with nothing on screen about it. The plan says the shopper sees
    "Nothing, or a slightly slower search".
  - The backend's log says once that the provider was switched off for the run.
- **For every provider down:** give each provider key a made-up value (`GROQ_API_KEY`, `GEMINI_API_KEY`,
  `MISTRAL_API_KEY`).
  - The interview still asks its fixed questions.
  - The results page says it couldn't reach the AI services, with a "Try again" button.
- **Put it back:** stop the stack and start it again without the variable.

## 2. A source: Reddit or web search down

- **The switch, Reddit off:** `REDDIT_SOURCE=off python scripts/dev.py --no-db`
- **The switch, web search down:**
  `SERPER_API_KEY=made-up-not-a-key FIRECRAWL_API_KEY=made-up-not-a-key RESEARCH_CACHE=off python scripts/dev.py --no-db`.
  Firecrawl stands in for Serper when Serper fails, so it gets a made-up key too. `RESEARCH_CACHE=off` makes the search
  ask again, as a search made in the last 24 hours would otherwise come from the cache and show nothing.
- **What to look for:**
  - Results from the other sources.
  - The note under the results names the source that failed.
  - The plan: "Results from the other sources, with a lower coverage figure and a note".
- **Put it back:** stop and start again without the variable.
- These use live searches, so they count against the day's caps (`backend/.cache/research/usage.json`). Run one
  search, not several.

## 3. The database: down or asleep

- **The switch, down:** `DATABASE_URL=postgresql://postgres:postgres@localhost:1/postgres python scripts/dev.py`.
  Nothing listens on port 1, so the database can't be reached.
- **The switch, off:** `python scripts/dev.py --no-db`.
- **What to look for:**
  - The search still works.
  - The results page shows "This result isn't saved. It stays at this address for up to 30 minutes, then it's gone."
  - http://localhost:8000/api/health says `"database": "down"`, or `"off"` with `--no-db`.
  - The plan: "The search still works; a banner says results will not be saved".
- **Put it back:** stop and start again without the variable.

## 4. The NLI model (the citation check)

- **The switch, the check off:** `NLI=off python scripts/dev.py --no-db`
- **The switch, the model failing to load:** the app keeps the model in its own folder, `backend/.cache/nli`, and loads
  from there first. With the stack stopped, rename that folder to `backend/.cache/nli-aside`, then start with downloads
  switched off: `HF_HUB_OFFLINE=1 python scripts/dev.py --no-db`. The model can't be found or fetched, so the load
  fails, as on a broken download.
- **What to look for:**
  - The explanations keep only sentences that quote their evidence directly. The plan: "Explanations use only sentences
    that quote evidence directly".
  - Nothing in the console: the backend notes this at INFO level, which `scripts/dev.py` doesn't show.
- **Put it back:** stop the stack, rename `backend/.cache/nli-aside` back to `backend/.cache/nli`, and start again
  without the variables.

## 5. Embeddings (remembered preferences)

- **The switch, memory off:** `MEMORY=off python scripts/dev.py`
- **The switch, the model failing to load:** as in drill 4, with the memory model's folder: rename
  `backend/.cache/memory` to `backend/.cache/memory-aside` with the stack stopped, then
  `HF_HUB_OFFLINE=1 python scripts/dev.py`.
- **What to look for:**
  - Signed in, the interview asks every question as usual, with no answer filled in from earlier searches. The plan:
    "No pre-filled answers this time".
  - With the model failing, the console shows one warning line starting `memory: nothing recalled`. With `MEMORY=off`
    there is none: memory is simply off.
- **Put it back:** stop the stack, rename the folder back if you moved it, and start again without the variables.

## The coverage table

One automated test per row of the plan's worst-case table (`project_docs/11_RISKS_WORST_CASE_AND_CONFLICTS.md`), in
`backend/tests/worst_case/test_worst_case.py`. Each runs the fault test that already proves its row, and its docstring
carries the row's own words for what the user sees.

| Row | Test | State | Checked |
|---|---|---|---|
| One LLM provider down or rate-limited | `test_one_llm_provider_down_or_rate_limited` | pass | 24 Nov 2026 |
| Every LLM provider down | `test_every_llm_provider_down` | pass | 24 Nov 2026 |
| The daily token budget used up | `test_the_daily_token_budget_used_up` | pass | 24 Nov 2026 |
| Reddit, YouTube or search down | `test_reddit_youtube_or_search_down` | pass | 24 Nov 2026 |
| A page blocks the reader | `test_a_page_blocks_the_reader` | pass | 24 Nov 2026 |
| The LLM returns broken JSON | `test_the_llm_returns_broken_json` | pass | 24 Nov 2026 |
| The matcher fails to load | `test_the_matcher_fails_to_load` | pass | 24 Nov 2026 |
| The NLI checker fails | `test_the_nli_checker_fails` | pass (S5-4 has merged) | 24 Nov 2026 |
| Embeddings fail | `test_embeddings_fail` | pass (S5-3 has merged) | 24 Nov 2026 |
| The database down or asleep | `test_the_database_down_or_asleep` | pass | 24 Nov 2026 |
| No product passes the hard rules | `test_no_product_passes_the_hard_rules` | pass | 24 Nov 2026 |
| Nothing found at all | `test_nothing_found_at_all` | pass | 24 Nov 2026 |
| The search passes 150 seconds | `test_the_search_passes_150_seconds` | pass | 24 Nov 2026 |
| The browser disconnects | `test_the_browser_disconnects` | pass | 24 Nov 2026 |
| A page hides instructions for the model | `test_a_page_hides_instructions_for_the_model` | pass | 24 Nov 2026 |

Where the app falls short of the plan's words, the test checks what the app does, and its docstring says so:

- **Every provider down, and the daily budget used up:** there is no result cache yet (US-34), so the shopper never
  gets a cached result. They get the clear message with "Try again".
- **Reddit, YouTube or search down:** there is no search-level coverage figure. The note names the failed source.
- **Nothing found at all:** no "not enough evidence" sentence exists yet. The note says why nothing was read, and no
  product is invented.

**In a browser:** `frontend/e2e/merge/request-to-results.spec.ts` goes from a typed request through the interview to
results, against the real backend with fake LLM providers and the database off
(`backend/tests/worst_case/fake_stack.py`). Its own workflow, `.github/workflows/merge-e2e.yml`, runs it on every push
to main. Each commit has its own run, never cancelled by the next push, so every merge is tested.
