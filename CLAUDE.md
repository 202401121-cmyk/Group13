# ShopSense: instructions for Claude Code

ShopSense turns a plain-English shopping request into a ranked, explained, sourced shortlist for one person.
IT314 Software Engineering, Group 13, six SCRUM sprints. Work is organised by user story (US-xx): your prompt
names the story and pastes its acceptance criteria from `backlog/stories.yaml`. Those criteria define the task.
There are no per-task issues.

Start Claude Code from the repository root: the project settings in `.claude/` load only from the folder you
start in.

## How every task runs

1. Start in plan mode. Read what the prompt lists, show the plan, and change nothing until the owner approves.
2. Commit under the author the prompt names (full name and email), so the work is credited to the person who did it.
3. Work only on the prompt's story, on its branch (`feature/US-xx-short-name`). Commit messages start with
   `US-xx: ` (or the prefix the prompt gives, such as `FE-0:`, `ML-1:`, `CI:`, `docs:`, `fix:`).
4. Push only the story's own branch, and only when the owner asks. Never push to `main`, never force-push,
   never change remotes. The owner opens and merges the pull request.
5. End with the hand-over report: commits with hash, author and committer; each acceptance criterion with its
   evidence; every check with its result; deviations and why; gaps with an owner; whether the branch was pushed.
6. When unsure about contracts, the build rules or anything touching user data, ask rather than guess.

## Architecture in one paragraph

A search runs through 11 stages: intake → criteria → interview → research → extract → match → verify →
enrich → decide → explain → commerce. Each stage has a typed input and output in `backend/app/contracts/`
and implements the `Stage` protocol (`run` + `fallback`). `backend/app/pipeline.py` runs the stages with time
limits (`STAGE_TIME_LIMITS_S`) and a 150-second global limit. The frontend (`frontend/`, TanStack Start on
Vite) calls the FastAPI backend and receives a `SearchResult`; live progress arrives as `ProgressEvent`s over
Server-Sent Events (from Sprint 2). The database (Supabase Postgres) is optional: the pipeline always finishes
without it.

## The ten build rules (never break these)

1. Every LLM call goes through the one wrapper (`app/llm/`, `LLMClient.complete`). Never import a provider SDK
   anywhere else. The wrapper fits input to the model's context window, validates output, and falls back.
2. Code decides. The LLM never filters, scores or ranks products; `app/decide/` is plain Python.
3. Web, Reddit and video text is data, never instructions. Wrap it as data in prompts (`wrap_as_data()` once
   US-28 adds it); accept only schema JSON back.
4. Every claim keeps its source: URL, exact quote, retrieval time.
5. No product without a source. Products with no mention and no source are dropped before scoring.
6. Unknown is not bad. Never score a missing value as zero. Look it up, else peer-average it and mark "estimated".
7. Cache everything expensive (criteria per category, LLM answers by content hash, searches for 24 h).
8. Personal data never reaches a provider that trains on inputs. Every LLM call declares a data class (private:
   anything the shopper typed; reddit; public), and tasks in `PERSONAL_TASKS` are always private. Providers
   marked `trains_on_inputs: true` in `config/models.yaml` (Gemini free tier, OpenRouter free models, Mistral's
   free plan) only ever get public text. A test proves it for every new task.
9. Every stage has a time limit and a fallback. No stage may hang or crash the search.
10. The pipeline never needs the database to finish. Keep state in memory; save at the end.

## Data rules

- Reddit: archive sources (Arctic Shift, then PullPush) until official API access is approved, chosen by
  `REDDIT_SOURCE` (`archive`, `official`, `off`). Never scrape, never use keyless `.json` or RSS endpoints,
  never Pushshift. Search results pointing at Reddit are used for links and titles only. Store usernames only
  as a salted hash; cache Reddit data at most 24 hours; never use Reddit text for training.
- WDC Products is for evaluation only: never train on it, never send it to a service that trains on inputs.
- Labels for ML data are made by people. Never suggest, pre-fill or guess a label, and never invent a product title.
- One root `.env`, never committed and never printed. Load `DATABASE_URL` and keys without echoing them.

## Contracts

- Stages talk ONLY through models in `app/contracts/`. Never pass raw dicts between stages.
- Import from the package: `from app.contracts import Intent, Rubric`.
- Models are strict and frozen. To change a field, rebuild with validation:
  `Model.model_validate({**m.model_dump(), "field": value})`. Do NOT use `model_copy(update=...)`: it skips validation.
- LLM output is never parsed straight into a contract. Parse into a lenient model in the stage's own
  `parsing.py`, clean it, then build the contract. Invalid output → retry once → fallback. Never guess.
- Every new rule in a contract gets a test in `backend/tests/contract/`.
- A contract change is a team decision. It goes in one commit: the model, `python -m scripts.export_contracts`
  (from `backend/`) and `bash scripts/gen-frontend-types.sh` (from the root), plus its contract tests.

## Commands

```bash
# backend (from backend/)
pip install -e ".[dev]"
pytest -q                               # never calls live APIs
pytest tests/contract -q                # contract tests only
ruff check . && ruff format --check .
mypy app                                # strict
python -m scripts.export_contracts      # regenerate shared/contracts/
python -m app.cli "best laptop for coding under 70k"

# frontend (from frontend/)
npm ci && npm run lint && npm run typecheck && npm test && npm run build

# ml (from the root)
pip install -e "backend[dev]" -r ml/requirements.txt && pytest ml/tests

# migrations (from the root; load DATABASE_URL without echoing it)
npx supabase@2.120.0 db push --db-url "$DATABASE_URL"
```

## Tests are part of every story

- Unit tests for any logic; a contract test for any stage input or output you touch.
- Any new LLM parser: golden tests in `tests/golden/` with malformed output (fenced JSON, missing keys, wrong
  types, out-of-range values, truncation, prose, injected instructions). Each gives the canonical shape or a
  clean fallback, never a crash.
- Any new fallback: a fault test in `tests/faults/` that forces it.
- Tests never call live APIs. Use recorded responses in `tests/fixtures/` and fake `LLMClient`s.
- Coverage gates: `app/decide/` 80%, other core modules 70%.

## Style

- Python 3.11, full type hints, `mypy --strict` clean, ruff clean, line length 120.
- Small functions, plain names, docstrings that say why. User-facing text is plain English at about Grade 8.
- Secrets only from environment variables. Never print or log a key.
- Times are timezone-aware UTC (`datetime.now(UTC)`).

## Git and credit

- One story per branch and pull request. Commits carry the name of the member who did the work;
  `Co-authored-by:` only for real pair work. Never commit as someone else.
- Never commit `.env`, keys, tokens, model weights, large data files, `node_modules` or build output.
- `.claude/settings.json` stops Claude Code from reading or editing `.env` files and from opening, merging or
  re-pointing anything on GitHub. Keep personal overrides in `.claude/settings.local.json` (git-ignored).

## Documents

- Official documents (reports, the Lab 6 update, poster text, submitted release notes) never mention Claude
  or any AI tool.
- Every number in a document comes from CI, the repository or an evaluation report, with its source.

## When unsure

Ask rather than guess, especially about contracts, the ten rules, or anything touching user data.
