# The database (Supabase Postgres)

ShopSense saves each shopper request, with its session ID, in a Supabase Postgres database (US-01, #14). It also has
a table for finished searches (US-52). The database is **optional**: with no `DATABASE_URL` everything works and
nothing is saved, and when the database is down, slow or paused a search still finishes (build rule 10).

## What is stored, and why

One row per shopper request, in `public.requests`:

| Column | What it holds |
|---|---|
| `id` | A random UUID |
| `session_id` | The session the request belongs to, for example `sess_3f9a…` |
| `request_text` | The request exactly as typed (1 to 1000 characters) |
| `intent` | What we understood (category, use, budget, rules), only when it was clear enough to research |
| `intent_source` | How it was understood: `llm` or `rules` (the keyword rules, when no LLM answered) |
| `created_at` | When it was saved |

- **Why:** to see what people really ask for, to build evaluation sets from real requests, and later to remember
  returning shoppers (US-06).
- **Not stored:** no IP address, user agent, name, email or login.
- **Private:** requests are what people typed and may be personal. Never send stored requests to Gemini's free tier
  or any other model service that may train on its inputs. Never paste rows into chat, Slack or an issue.

Row level security is on with no policies, and the table is revoked from `anon` and `authenticated`. So Supabase's
Data API (the anon and logged-in browser keys) cannot read or write it. Only the backend's own database login can.

### Finished searches

One row per finished search, in `public.searches` (US-52):

| Column | What it holds |
|---|---|
| `id` | A random UUID. This is not `SearchResult.search_id`, which is inside `result` |
| `session_id` | The session the search belongs to, with the same check as `public.requests` |
| `rubric` | The rubric the search used (`SearchResult.request.rubric`) |
| `result` | The whole `SearchResult` as JSON, exactly as `model_dump(mode="json")` writes it |
| `created_at` | When it was saved |

- **Why:** the pipeline keeps its state in memory and saves once at the end (US-52, build rule 10).
- **No foreign key to `public.requests`:** saving a request can fail while the search saves later, and that search
  must still be kept. A row in one table never needs a row in the other.
- **More than one per session:** one session can run more than one search, so `session_id` has an index but is not
  unique.
- **Not stored:** no IP address, user agent, name, email or login.
- **Private:** `result` holds the request as typed, so this is private data, like `public.requests`. It stays in our
  database only. Never send it to Gemini's free tier or any other model service that may train on its inputs, and
  never paste rows into chat, Slack or an issue.
- **No expiry yet (gap, US-12, Sprint 2):** rows stay until someone deletes them. Real results will hold Reddit
  quotes, and Reddit data may be kept at most 24 hours. US-12 must deal with this before real Reddit text reaches
  this table.
- **Guests (decision D-36):** there is no sign-in yet, so every row is a guest's. A guest's request and search are
  stored only under the anonymous session ID, are never linked to an account, and are never used to remember
  preferences.

Row level security is on with no policies, and the table is revoked from `anon` and `authenticated`, the same as
`public.requests`. So the Data API cannot read or write it. **Who can read it:** the backend through `DATABASE_URL`,
and project members through the Supabase dashboard. Sprint 4 (#52) adds an owner column and policies for signed-in
users.

## Setting up the project (once, by hand, in the browser)

1. On supabase.com, create a project on the **Free** plan, region **South Asia (Mumbai)**.
   - Generate a strong database password and keep it in a password manager.
   - Invite Om (repo admin) and Jenish to the organisation.
2. Open **Project > Connect** and copy the **Session pooler** connection string.
   - Don't use the direct connection: on the Free plan it is IPv6 only and fails on most home and college networks.
     The session pooler works over IPv4.
3. Put it in the root `.env` (the same file as the LLM keys), with `?sslmode=require` at the end:
   ```
   DATABASE_URL=postgresql://postgres.<project-ref>:<password>@<session-pooler-host>:5432/postgres?sslmode=require
   ```
   - The host is the one shown under Session pooler in the dashboard.
   - Never paste the URL or the password into chat, Slack, an issue or a commit. `.env` is git-ignored.
   - Don't create `backend/.env`: the backend would find it first and stop reading the root `.env`.
4. Note the project's Postgres major version (**Settings > Infrastructure**). It is **17**; the tests use the same.
5. Apply the migrations (next section), then open **Advisors > Security**. For `public.requests` it should show only
   **RLS Enabled No Policy** (INFO). That finding is expected: see the live check below.

`DB_TIMEOUT_SECONDS` (default 2) is how long one save may take before the search carries on without it.

## Migrations

Every schema change is a new SQL file in `supabase/migrations/`, run in timestamp order. Never edit one that is
already merged; write a new one (see `supabase/migrations/README.md`). The Data & Evaluation Engineer reviews them.

Add one, from the repository root, with the pinned Supabase CLI:

```bash
npx supabase@2.120.0 migration new add_example_table   # use your own short name
```

Apply them to the Supabase project, from the repository root with the backend's virtual environment active (the
first line needs `python-dotenv`). The commands read `DATABASE_URL` from `.env` without printing it.

Git Bash:

```bash
export DATABASE_URL="$(python -c 'from dotenv import dotenv_values; print(dotenv_values(".env")["DATABASE_URL"])')"
npx supabase@2.120.0 db push --db-url "$DATABASE_URL"
unset DATABASE_URL
```

PowerShell:

```powershell
$env:DATABASE_URL = python -c "from dotenv import dotenv_values; print(dotenv_values('.env')['DATABASE_URL'])"
npx supabase@2.120.0 db push --db-url "$env:DATABASE_URL"
Remove-Item Env:DATABASE_URL
```

### After `create_searches` merges (once, by hand)

The migration `20261106160150_create_searches` is only a file until someone applies it.

1. Vaishvi applies it in her own terminal with `db push`, as above, and tells Om.
2. Om checks these read-only: the migration history lists it, row level security is on for `public.searches`, it has
   zero policies, `anon` and `authenticated` have no access, and what the Security Advisor shows.
3. Expect one new Security Advisor finding, **RLS Enabled No Policy** on `public.searches` (INFO). It is the same
   intended design as `public.requests` (see "Live check" below).

**Apply it before the pipeline starts saving searches.** Without the table every search save fails. Search saves and
request saves share one circuit breaker, so 3 failed searches in a row also stop request saves for 30 seconds, and
health says `down` even though the database is fine.

## "off" and "down"

`GET /api/health` answers `"database": "ok"`, `"off"` or `"down"`. Nothing else about the database is shown.

| | Meaning | What happens |
|---|---|---|
| `ok` | The database answered | Requests are saved |
| `off` | No `DATABASE_URL` is set | Everything works; nothing is saved |
| `down` | It is set, but the database did not answer in time (wrong password, network, paused project) | Searches still finish; the request is not saved |

Each save waits at most `DB_TIMEOUT_SECONDS`. After 3 failures in a row the backend stops trying for 30 seconds
(saves fail at once, health says `down`), then tries once more. The session remembers the outcome in
`request_saved`: `saved`, `off` or `failed`. The CLI prints one line, and `--no-db` skips the database.

Logs show only the database host and the error type, never the URL, the password or the request text.

### Saving a finished search

`save_search(session_id, rubric, result)` is on every request store, next to `save_request`, and shares its connection
pool, time limit and circuit breaker. `rubric` and `result` are `model_dump(mode="json")` output. A plain
`model_dump()` holds dates and URLs, which are not JSON, so it is refused. `DatabaseResultStore` in
`backend/app/pipeline.py` calls it at the end of every search, from both the API and the CLI.

It answers `True` only when the row was committed. It never raises, and it answers `False` with one log line when:

- the database is off (this line is at info level, because it is a normal way to run);
- the database is down, or slower than `DB_TIMEOUT_SECONDS`;
- the circuit breaker is open, or the store is closed;
- the insert fails;
- the search cannot fit the table: a bad session ID, something that is not JSON, a NaN or infinite number, or the NUL
  character, which `jsonb` cannot hold. These are refused before the database is touched, so one bad result never
  counts towards the circuit breaker.

An insert that finishes after the time limit is rolled back, so a late row does not appear after the answer `False`.
`False` still means "not confirmed" rather than "certainly not there": if the limit passes or the connection drops
during the commit itself, the row can exist. Nothing retries, so this can never make a second row.

## The Free plan pauses idle projects

A Free project is paused after 7 days of low activity. A paused project shows as `down`, and nothing is saved.

- Resume it from the Supabase dashboard (the project page has a **Restore** button). It takes a few minutes.
- **Open the dashboard the day before every review**, so a pause never surprises a demo.

## Changing the password

1. In the dashboard: **Settings > Database > Reset database password**. Use a new strong password from the password
   manager.
2. Update `DATABASE_URL` in the root `.env` on every machine that has it, and in any CI secret that holds it.
3. Restart the backend. Until then it shows `down` (the old password no longer works).

## Tests

- **Unit and drill tests** need no database and run everywhere (`pytest`). The drills point the backend at a closed
  local port and at a server that never answers, and check that searches still finish and health says `down`.
- **Integration tests** (marker `db`) need a throwaway Postgres 17. They skip without `TEST_DATABASE_URL`. With
  `REQUIRE_DB_TESTS=1` a missing database fails them instead. They never run against Supabase: each run creates its
  own database, applies every migration, checks the tables, row level security and the limits, and drops it. The
  `backend` job in CI runs them on every pull request against `postgres:17`, with `REQUIRE_DB_TESTS=1`.
- A plain Postgres gives `anon` and `authenticated` nothing, so checking their privileges there proves little. One
  test in `test_searches_table.py` copies Supabase's default grants first, so a lost `revoke` makes it fail.

You need Docker. Run this from the repository root, with the backend's virtual environment active. The password is
made up for this one run, and the database is open only to your own machine.

```bash
PGPASS=$(python -c "import secrets; print(secrets.token_hex(16))")
docker run -d --name shopsense-pg17 -e POSTGRES_PASSWORD="$PGPASS" -p 127.0.0.1:55432:5432 postgres:17
until docker exec shopsense-pg17 pg_isready -q -h 127.0.0.1 -U postgres; do sleep 1; done   # wait until it is ready
cd backend
TEST_DATABASE_URL="postgresql://postgres:$PGPASS@127.0.0.1:55432/postgres" pytest -m db -rs
cd ..
docker rm -f shopsense-pg17
unset PGPASS
```

## Live check

Run against the team's Supabase project on **3 Nov 2026** (US-01). No URL, host or password is recorded here.

| Check | Result |
|---|---|
| Postgres version | **17.11** (`show server_version`); `major_version = 17` in `supabase/config.toml` already matched |
| Migration | `npx supabase@2.120.0 db push`: "Remote database is up to date". `20261026170303 create_requests` is listed in `supabase_migrations.schema_migrations` |
| Table | `public.requests` exists |
| Row level security | `relrowsecurity` is true |
| Data API roles | `has_table_privilege('anon', 'public.requests', 'select')` and `has_table_privilege('authenticated', 'public.requests', 'insert')` are both false |
| Health | `GET /api/health` answered `"database": "ok"` |
| Saving | One made-up request through the CLI and one through `POST /api/presearch/intake`: exactly 2 rows for their session IDs, both with `intent_source` `llm`. They stayed in the table until the follow-up below deleted them |
| Down drill (wrong password) | Health answered `"database": "down"`. The intake request still answered (HTTP 200 in 3.5 s) and a full CLI search finished (exit 0, about 13 s) with "Saved: no, the database could not be reached. The search goes on." Nothing was saved. The logs show only the host and the error type (`PoolTimeout`, `TimeoutError`, then "skipping the database for 30 s"): no traceback, URL or password |

**Security Advisor** (Advisors > Security, read in the dashboard the same day):

- `public.requests`: **RLS Enabled No Policy**. Row level security is on and has no policies on purpose, and the
  table is revoked from `anon` and `authenticated`, so the Data API can neither read nor write it (the checks
  above). This is the intended design, and the setup step above expects it. The team decides whether to keep it
  as is or add an explicit deny-all policy in a new migration.
- `public.rls_auto_enable()`: **Public Can Execute SECURITY DEFINER Function** and **Signed-In Users Can Execute
  SECURITY DEFINER Function**. Supabase added this function when "automatic RLS" was chosen at project creation.
  Its execute rights are revoked by its own migration ("US-01: lock down the automatic RLS helper function").

### Follow-up, 4 Nov 2026 (W1b)

- **Helper locked:** migration `20261103102906_lock_rls_auto_enable` revoked execute on `public.rls_auto_enable()` from
  `public`, `anon`, `authenticated` and `service_role`. `has_function_privilege('anon', 'public.rls_auto_enable()',
  'execute')` is false.
- **Test rows deleted:** the table held exactly five rows, all made-up test requests. They were the two from the
  check above and three from the full-stack live test and the Sprint 1 review check (3 Nov 17:56 and 17:57 UTC,
  4 Nov 15:47 UTC). Only `session_id` and `created_at` were read. They were deleted by their five session IDs, and
  `select count(*) from public.requests` now returns **0**.
- **Security Advisor**, read the same day: one finding, **RLS Enabled No Policy** on `public.requests` (INFO). The two
  `rls_auto_enable()` warnings are gone. That remaining finding is the intended design: row level security is on,
  there are no policies, and `anon` and `authenticated` have no access, so only the backend (through
  `DATABASE_URL`) can read or write the table.
- **Every later live test** run with the database on adds one row for its session. Delete such rows by session ID
  the same way (read only `session_id` and `created_at` first).
