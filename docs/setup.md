# Set up and run ShopSense

Everything needed to run the whole app (backend, frontend and, if you want it, the database) from a fresh clone,
check your machine, and prove it works end to end. Commands are for Git Bash unless a PowerShell line is given.

## 1. Prerequisites

| Tool | Version | Check |
|---|---|---|
| Git | any recent | `git --version` |
| Python | 3.11 or later (CI uses 3.11) | `python --version` |
| Node | 20.19 or later (Vite needs it) | `node --version` |
| npm | comes with Node | `npm --version` |

On Windows, use Git Bash or PowerShell, and keep the clone and its virtual environment in a short path (for
example `C:\dev\Group13`): some Python packages (numpy, psycopg) fail to install under very long paths
(WinError 206). The other fix, turning on Windows long paths once, is in [ml/README.md](../ml/README.md).

## 2. Clone and install

```bash
git clone https://github.com/202401121-cmyk/Group13.git
cd Group13

# Backend (a virtual environment in backend/.venv, which scripts/dev.py finds by itself)
cd backend
python -m venv .venv
source .venv/Scripts/activate      # Git Bash on Windows; macOS and Linux: source .venv/bin/activate
                                   # PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
cd ..

# Frontend (installing also generates the contract types)
cd frontend
npm ci
cd ..

# ML workspace (only if you work on ml/), with the backend's venv active
pip install -r ml/requirements.txt
```

`pip install -r requirements.txt` from the root installs the backend and the ML tools in one go.

## 3. Settings

Copy `.env.example` to `.env` in the **repository root** (never `backend/.env`), and fill in only what you need.
Everything is optional: with no keys at all the app still runs, using its keyword rules and fixed criteria, and with
no `DATABASE_URL` nothing is saved. Where each key comes from: [api-setup.md](api-setup.md). The database:
[database.md](database.md).

`scripts/dev.py` sets the frontend's two variables for you. To run `npm run dev` by hand in live mode, copy
`frontend/.env.example` to `frontend/.env.local`.

### Every variable

| Variable | Read by | Needed? | Example or default | Secret? |
|---|---|---|---|---|
| `FRONTEND_ORIGINS` | `backend/app/main.py` (CORS) | optional | `http://localhost:3000` | no |
| `FRONTEND_ORIGIN` | `backend/app/main.py` (the older one-origin name) | optional | — | no |
| `GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `CEREBRAS_API_KEY`, `MISTRAL_API_KEY` | the LLM wrapper (`backend/app/llm/`, names in `backend/config/models.yaml`); the smoke test | optional (without any, keyword rules and fixed criteria) | — | **yes** |
| `MISTRAL_MODEL`, `MISTRAL_RPS`, `MISTRAL_TPM`, `MISTRAL_DAILY_TOKENS` | `backend/app/llm/config.py` | optional | `ministral-8b-latest`, `2`, `500000`, `1000000` | no |
| `SERPER_API_KEY`, `YOUTUBE_API_KEY`, `JINA_API_KEY`, `HF_TOKEN` | `backend/scripts/smoke_apis.py` (the stages that use them come in later sprints) | optional | — | **yes** |
| `REDDIT_SOURCE`, `REDDIT_ARCHIVES`, `ARCTIC_SHIFT_BASE_URL`, `PULLPUSH_BASE_URL` | `backend/app/research/reddit/settings.py` | optional | `archive`, `arctic_shift,pullpush`, the two archive URLs | no |
| `REDDIT_USER_SALT` | `backend/app/research/reddit/settings.py` | optional (no salt: no author hash is kept) | — | **yes** |
| `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` | `backend/app/research/reddit/settings.py`; the smoke test | only with `REDDIT_SOURCE=official` | — | ID and secret: **yes** |
| `DATABASE_URL` | `backend/app/db/settings.py`; `scripts/doctor.py`; `scripts/check_request_saved.py` | optional (empty: the database is off) | the Session pooler string, see [database.md](database.md) | **yes** |
| `DB_TIMEOUT_SECONDS` | `backend/app/db/settings.py` | optional | `2` | no |
| `APP_ENV`, `LOG_LEVEL`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` | nothing yet (kept for later stories, such as login in Sprint 4) | no | — | service-role key: **yes** |
| `VITE_API_BASE_URL` | `frontend/src/lib/api/client.ts` | optional | `http://localhost:8000` | no (it reaches the browser) |
| `VITE_API_MODE` | `frontend/src/lib/api/client.ts` | optional | `mock` (default) or `live` | no |
| `GIT_BASH` | `frontend/scripts/contract-types.mjs` | optional (Windows only, if Git Bash is not found) | path to `bash.exe` | no |
| `PORT` | the built frontend server (`npm start`) | optional | `3000` | no |
| `TEST_DATABASE_URL`, `REQUIRE_DB_TESTS` | `backend/tests/integration/` (tests and CI only) | tests only | a throwaway local Postgres; `1` | no |
| `RUN_LIVE_TESTS` | `backend/tests/live/` | tests only | `1` | no |
| `LIVE_E2E`, `LIVE_BASE_URL` | `frontend/e2e/live/` | live test only | `1`; `http://localhost:3000` | no |

Every name above was found in the code with a search, and every name the code reads is in this table. The one
exception is `SHOPSENSE_DOCTOR_DB`, which `scripts/doctor.py` sets for itself during its database check; never set
it by hand. Every name in `.env.example` is in this table, and the two `VITE_` names are in `frontend/.env.example`.
The rest are in neither file: the older `FRONTEND_ORIGIN`, `GIT_BASH`, `PORT`, and the names for tests only.

## 4. Check your machine

With the backend's venv active, from the repository root:

```bash
python scripts/doctor.py          # every check
python scripts/doctor.py --ping   # also calls each configured service once, except Serper (the API smoke test)
```

It prints OK, WARN or FAIL for: Python and Node versions; the backend and frontend installs; the root `.env` (found
and ignored by git) and no `backend/.env`; which keys are set (names only, never values); the database (reachable,
with its Postgres major version); contracts and frontend types up to date; the frontend's settings; CORS; and
whether ports 8000 and 3000 are free. Every problem comes with a one-line fix, and it exits 0 when nothing FAILs.
WARN rows don't stop anything: for example, without a database the app still runs.

## 5. Run everything with one command

```bash
python scripts/dev.py              # backend on :8000, frontend on :3000 in live mode
python scripts/dev.py --no-db      # the same, with the database off (nothing is saved)
python scripts/dev.py --mock       # only the frontend, with its built-in sample data (no backend, no keys)
python scripts/dev.py --frontend-port 3001   # another port; the backend's CORS follows it
python scripts/dev.py --backend-port 8001    # another port; the frontend calls the backend there
```

It waits up to 60 seconds for both parts, then prints the two addresses, the database state (`ok`, `off` or
`down`) and how many LLM providers have a key. Open the frontend address in a browser. **Ctrl+C stops both**,
including everything they started. It refuses to start, and says which port, if either port is already in use.

To run the parts by hand instead: `cd backend && uvicorn app.main:app --port 8000` with the venv active, and in
another terminal `cd frontend && npm run dev` (with `frontend/.env.local` set to live mode).

## 6. The live end-to-end test

It types a made-up request ("Laptop for coding under 80k") into the real app, answers the follow-up if one comes,
checks the understood screen and the criteria, answers at least 5 interview questions, reviews the brief, waits for
the results (up to 170 seconds) and checks the ranked list and its sources. It prints the session ID and each
step's time. It never runs in CI or in `npm run test:e2e`.

With `scripts/dev.py` running, from `frontend/`:

```bash
npx playwright install chromium    # once: the browser the test uses
LIVE_E2E=1 npx playwright test -c e2e/live/playwright.live.config.ts
# PowerShell: $env:LIVE_E2E="1"; npx playwright test -c e2e/live/playwright.live.config.ts
# another frontend port: add LIVE_BASE_URL=http://localhost:3001
```

Then check that the request was saved, using the printed session ID (from the root, venv active):

```bash
python scripts/check_request_saved.py sess_0123456789abcdef01234567    # prints "saved" or "not found"
```

With no `DATABASE_URL` in the root `.env` there is nothing to check: it says so and exits with code 2.

Run it twice: with `python scripts/dev.py` (expect "saved") and with `python scripts/dev.py --no-db` (the flow
still finishes; expect "not found"). Each run adds one row with a made-up request to the team database.

## 7. Drills: the app keeps working when parts fail

Each drill changes the environment of one `scripts/dev.py` run only. Never edit the real `.env` for a drill.

**One LLM key off.** Blank the first provider in the private route (Groq), run the live test: the flow still
finishes, on the next provider or the stage's fallback. Repeat with a second key.

```bash
GROQ_API_KEY= python scripts/dev.py
# PowerShell: $env:GROQ_API_KEY=" "; python scripts/dev.py     (a space: an empty value would remove the variable)
```

**Every LLM key off.** Blank all five: intake falls back to keyword rules, criteria to the fixed sets, and the
shopper still gets results or a clear message, never a crash.

```bash
GROQ_API_KEY= GEMINI_API_KEY= OPENROUTER_API_KEY= CEREBRAS_API_KEY= MISTRAL_API_KEY= python scripts/dev.py
```

**Database down.** Point `DATABASE_URL` at a closed port: the summary and `GET /api/health` say `down`, and the
live test still passes.

```bash
DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:9/postgres" python scripts/dev.py
curl -s http://localhost:8000/api/health        # in a second terminal: "database":"down"
```

## 8. Troubleshooting

| Problem | What to do |
|---|---|
| The browser console shows a CORS error | The page's origin must be in `FRONTEND_ORIGINS` in the root `.env` (comma-separated). `scripts/dev.py --frontend-port N` adds `http://localhost:N` for you. Restart the backend after changing it. |
| "Port 8000 (or 3000) is already in use" | Something still runs there, often an old dev server. Close its terminal, or find it (`netstat -ano \| findstr :3000` in PowerShell, `lsof -i :3000` on macOS and Linux) and end it, or use `--backend-port` or `--frontend-port`. |
| Health says the database is `down` | A free Supabase project pauses after 7 days without use: resume it in the dashboard. Then run `python scripts/doctor.py`. Searches still finish meanwhile; nothing is saved. |
| The database never connects from home or college Wi-Fi | Use the **Session pooler** connection string (port 5432, `?sslmode=require`), not the direct connection: the direct host is IPv6-only, and many networks have no IPv6. See [database.md](database.md). |
| An LLM provider answers 429 | Its free limit is used up for now. The app moves to the next provider and waits out the cool-down; see [api-setup.md](api-setup.md) for each limit. Cerebras answers 402 because it has no free tier any more. |
| `pip install` fails on numpy or psycopg on Windows | The path is too long. Clone to a short path such as `C:\dev\Group13`, or put the venv there. |
| A command works in Git Bash but not in PowerShell | Inline variables (`NAME=value command`) are Git Bash syntax. In PowerShell set `$env:NAME="value"` first, then run the command. Activate the venv with `.venv\Scripts\Activate.ps1`. |
| `npm ci` fails with peer-dependency errors | Run it inside `frontend/`, where `.npmrc` sets `legacy-peer-deps`. |
| The frontend shows sample laptops only | It is in mock mode. Use `scripts/dev.py` (live mode), or set `VITE_API_MODE=live` in `frontend/.env.local`. In Sprint 1 the live search itself still returns example laptops; real research arrives in Sprints 2 and 3. |

## 9. Test without the internet

**A plain `pytest` is the fast run.** From `backend/`, it leaves out the tests marked `live` and the ones marked `slow`,
which really wait for a timeout or a breaker. CI runs the slow ones too, because it passes its own `-m "not live"`.

```bash
pytest                                                      # the fast run: under 60 seconds
pytest -m slow                                              # only the tests that really wait
pytest -m "not live" --cov && python -m scripts.check_coverage   # everything, then the coverage gates
```

The gates need 70% on each core module and 80% on `app/decide/`. `app/api/`, `app/cli.py` and `app/main.py` are entry
points, so only the overall 70% covers them.

**The network is blocked in tests.** A connection, DNS lookup or UDP send to anything but this machine fails at once
with "Tests may not use the network", and fails the test even if the code caught the error. Only tests marked `live` are
exempt. The block has limits:

- It covers the Python process running the tests. Subprocesses open their own sockets, and so does the `psycopg` database
  driver when the host is an IP address. Database tests use `TEST_DATABASE_URL`, and every other test gets the "off" store.
- It starts with each test, so code that runs at import time, or in a module or session fixture, comes before it.
- `asyncio` on Windows connects in a way it can't see. The app has no async HTTP client, so nothing uses that today.

**Replay a recording.** Recordings live in `backend/tests/fixtures/recorded/<name>/`, one file per service, with a
`README.md` line saying what each one is, when it was recorded and by whom.

```python
from tests.fakes.recorded import RecordedAdapter, load_recording, recorded_transport

recording = load_recording("laptop_coding_70k")
adapters = {Provider.GROQ: RecordedAdapter(recording, Provider.GROQ)}   # for WrapperLLMClient
http = httpx.Client(transport=recorded_transport(recording, "serper"))  # for a source adapter
```

Calls are answered in the order they were recorded, and a call that failed or was refused replays as a failure. A
recording that runs out fails the test.

**Make a recording.** This spends real quota, so use it on purpose. Use a made-up request only.

```bash
python -m scripts.record_run "best laptop for coding under 70k" --name laptop_coding_70k --by "Your Name"
python -m scripts.record_run "..." --name other --by "Your Name" --runs 2 --budget mistral=2 --force --keep-failures
```

| Limit | What it does |
|---|---|
| Runs | At most 5 per invocation. |
| Budget | Printed before the first call and enforced on every call. Groq gets 6 and every other service 0, so Serper and YouTube are never called unless you add `--budget`. |
| Reddit | Never called or fetched. A budget above 0 is refused, and so is a `reddit.com`, `old.reddit.com`, `redd.it` or Reddit archive URL through any service, because Reddit data never goes into the repository (D-53 caps Reddit caching at 24 hours). Set `REDDIT_SOURCE=off` for a recording once research calls Reddit. With `--keep-failures`, a refused call is written as a row with no reply: just the service, the URL with no query values or Reddit path, and the error. |
| Over budget | The call is never sent. It fails like a network error, so the stage's fallback answers. |
| Unknown host | Refused the same way. |
| A stage that times out | Its thread keeps running, so the recorder waits for the last call to end and refuses anything new once the app has finished. |
| Existing recording | Kept unless you add `--force`. |
| A run that failed | Nothing is written, so a bad key can't replace a good recording. Every run must end with code 0, every call must answer 2xx and none may be refused. If the app tried a service with no budget, such as Mistral for a request with no use case, add `--budget mistral=1`. Add `--keep-failures` to record a failure on purpose: refused calls are then kept as failures, and the manifest lists what went wrong. |
| `REDDIT_SOURCE=official` | Refused, because PRAW doesn't go through `httpx`, so it could not be capped or redacted. |

It runs the real CLI with `--no-db` and an in-memory criteria store, so no database is touched and saved criteria can't
hide a call. It doesn't read the Reddit cache either. Nothing is written until the run has ended and every call has been
cleaned:

- **Headers:** only `content-type`, `x-ratelimit-*` and `retry-after` are kept.
- **Query strings:** names are kept, values become `[redacted]`.
- **Environment:** every value of a secret-named variable, and every other value of 8 or more characters, is replaced.
  That includes plain settings: `APP_ENV=development` turns "software development" into "software [redacted]". The names
  of the variables are printed with a count, so check them. If a value still survives, nothing is written. A plain
  variable holding exactly the name you give with `--by` is kept. Shorter values can't be told from ordinary text, so
  they stay.
- **Search results that link to Reddit:** the link keeps only its scheme and host, so it reads
  `https://www.reddit.com/[reddit link not stored]`. The title and snippet become `[reddit text not stored]`.
- **Reddit links elsewhere:** a plain link in a prompt, a reply or a web page keeps only its scheme and host the same
  way. An encoded link can slip past, which is one more reason to read a recording before you commit it.
- **Text from Reddit elsewhere:** an LLM call declared `DataClass.REDDIT`, or whose prompt carries Reddit text, has its
  prompt and whole reply replaced, and that reply then counts as Reddit text too. Any other run of 5 or more matching
  words is replaced wherever it appears. The shopper's own request never counts. The number of calls replaced is printed.

So a recording can't give a test Reddit text. Tests that need it use a small synthetic thread, like the ones in
`backend/tests/fixtures/reddit/`, marked synthetic.

If stages time out while recording, check the connection before you spend more quota: it prints each call's time.

Read the files before you commit them, and treat the recording as public: the prompts and replies go into the repository.
