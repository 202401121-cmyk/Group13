# Deploying ShopSense to staging (DEP-1)

Staging is a free copy of the app on the internet. It lets hosting problems surface now instead of at the demo (P-08).
It also shows two things: that a search never needs the database (build rule 10), and whether the matcher fits the
free backend's memory (P-10).

| Part | Where | Plan | Settings in the repository | Owner |
|---|---|---|---|---|
| Backend (FastAPI) | Render web service | Free | [`render.yaml`](../render.yaml) | Pal |
| Frontend (TanStack Start) | Vercel project | Hobby | `frontend/vercel.json`, `frontend/vite.config.ts` | Yashkumar |
| Database | Supabase (the team's project) | Free | [database.md](database.md) | Om |

**Secrets live only in the Render and Vercel dashboards.** The repository holds the names of the variables, never
their values. Never paste a key into a file, an issue or a chat.

## 1. The free tiers (checked on 14 Nov 2026)

These shape how staging behaves. Check them again before the demo: free plans change.

**Render, free web services**
- [Free instances](https://render.com/docs/free):
  - a service spins down after **15 minutes without inbound traffic**;
  - the next request waits **about one minute** while it starts again;
  - there are **750 free instance hours** per workspace each calendar month;
  - there is no persistent disk, and the filesystem is ephemeral: files written while running are lost on every
    redeploy, restart or spin-down;
  - Render may restart a free service at any time.
- [Blueprint spec](https://render.com/docs/blueprint-spec): the `free` plan is **0.1 CPU and 512 MB**. A variable
  marked `sync: false` is asked for in the dashboard once, when the Blueprint is created; later Blueprint syncs
  ignore it.
- [Python version](https://render.com/docs/python-version): services created after 3 Nov 2026 default to **Python
  3.14.10**. That's why `render.yaml` sets `PYTHON_VERSION` to a full 3.11 version, the one CI tests.
- [Rollbacks](https://render.com/docs/rollbacks): a rollback reuses an earlier deploy's build, start command and
  environment variables. **A rollback from the dashboard turns automatic deploys off** until they are switched on again
  in Settings.

**Vercel, Hobby plan**
- [Hobby plan](https://vercel.com/docs/plans/hobby) (page updated 12 Nov 2026):
  - it is free;
  - functions run for at most **300 seconds**;
  - each project gets **100 deployments a day**;
  - runtime logs are kept for 1 hour.
- Hobby is for personal, non-commercial use only. Vercel's [Terms of Service](https://vercel.com/legal/terms) and
  [fair use guidelines](https://vercel.com/docs/limits/fair-use-guidelines) count payments, ads, affiliate links and
  paid work as commercial. A student project with none of those fits.
- [TanStack Start on Vercel](https://vercel.com/docs/frameworks/full-stack/tanstack-start) (page updated 14 Nov
  2026): the app needs Nitro's Vite plugin. [Nitro's Vercel page](https://nitro.build/deploy/providers/vercel) names
  the preset `vercel`.
- [Root Directory](https://vercel.com/docs/builds/configure-a-build#root-directory): by default, a build can read
  files outside the project's folder. The frontend needs this, because its build reads `shared/contracts/`.
- [Instant Rollback](https://vercel.com/docs/instant-rollback): on Hobby, only to the previous production
  deployment. Afterwards, new pushes don't go live until the rollback is undone.
- Vercel's [deploy guide](https://vercel.com/kb/guide/deploy-a-tanstack-start-app-to-vercel) (updated 14 Oct 2026):
  - Vercel usually detects the framework, and `vercel.json` with `"framework": "tanstack-start"` makes it certain;
  - it asks for Node.js 24 or later;
  - a `VITE_` variable is bundled into the code the browser downloads, so it must never hold a secret.

**What this means for us**
- **The first request after 15 idle minutes takes about a minute.** The page's first call (the request) gives up
  after 12 seconds, so a sleeping backend makes the first search fail. **Before showing staging, open
  `/api/health` and wait for its answer.** The smoke check (section 5) times this.
- **Every spin-down empties the caches**, because the filesystem is ephemeral. That covers saved criteria, the
  research cache, the Reddit cache and the downloaded matcher model (91 MB). So the first search after a start is
  slower.
- **Every spin-down also resets the usage limits.** `backend/config/research.yaml` caps each provider per search, per
  day and in total, but it keeps those counts in `backend/.cache/research/usage.json`, and that file goes too. The
  LLM providers' daily token budgets are kept in memory, so a restart resets them as well.
  - On staging the limits therefore hold only between two spin-downs. The Serper total of 2,400 can't build up, and
    each start allows another day's worth: 60 Serper queries and 30 Firecrawl credits.
  - Staging has no login, so anyone with the address can start searches that spend the team's keys.
  - So:
    - give staging its own Serper key if you can;
    - leave `JINA_API_KEY` and `FIRECRAWL_API_KEY` empty on staging (plain page reads still work);
    - suspend the service from its Settings page when nobody is checking it;
    - share the address only within the team until login exists (D-65, US-30).
- **512 MB is shared by every search running at once**, so staging runs one search at a time (`SEARCH_RUN_WORKERS=1`).
  A second search gets the app's "busy, try again" answer.
- **Every search is saved** (D-36), and staging saves into the team's Supabase: **staging keeps `DATABASE_URL`**
  (D-65, 14 Nov 2026), so saving is proved on the real database. A stranger's search would save the request they
  typed, which is private data (D-51). So the address stays within the team until login (US-30), and every session
  the team's own checks create is listed in section 6 and deleted afterwards.

## 2. Backend on Render (Pal)

### Create the service
1. Make sure `render.yaml` is on `main` (merge this change first).
2. In the Render dashboard, choose **New > Blueprint** and connect the GitHub repository. Pick the `main` branch.
   Render reads `render.yaml` and shows one web service, `shopsense-backend-staging` (free, Singapore).
3. Render asks for each variable in the table below. Enter the staging values. A service that staging doesn't use can
   be left empty.
4. Create it. The first build installs the backend (`pip install -e .`, in `backend/`) and takes a few minutes.

`render.yaml` sets the rest:
- Python 3.11 (`PYTHON_VERSION`);
- the start command, `uvicorn app.main:app --host 0.0.0.0 --port $PORT`;
- the health check, `/api/health`;
- automatic deploys of each commit on `main` whose CI checks pass (`checksPass`). Render deploys only when a commit
  changes something under `backend/` (the service's root folder); a docs or frontend change doesn't redeploy it.

### The variables
These are the variables `render.yaml` names. `backend/tests/unit/test_render_blueprint.py` checks that this table
and the file name the same ones.

<!-- render-variables -->
| Variable | Staging value | Secret? |
|---|---|---|
| `FRONTEND_ORIGINS` | The Vercel production address, such as `https://<project>.vercel.app`, with no slash at the end. Commas separate more than one. Never `*`. | no |
| `GROQ_API_KEY` | The team's Groq key | **yes** |
| `GEMINI_API_KEY` | The team's Gemini key | **yes** |
| `OPENROUTER_API_KEY` | The team's OpenRouter key | **yes** |
| `CEREBRAS_API_KEY` | The team's Cerebras key | **yes** |
| `MISTRAL_API_KEY` | The team's Mistral key | **yes** |
| `DEEPSEEK_API_KEY` | The team's DeepSeek key, on its granted balance only: never add a payment method (D-11). Empty leaves DeepSeek out of the public fallback (US-46). | **yes** |
| `SERPER_API_KEY` | The team's Serper key | **yes** |
| `YOUTUBE_API_KEY` | The team's YouTube Data API key, restricted to that API. Empty leaves YouTube out of every search (US-38). | **yes** |
| `JINA_API_KEY` | Leave empty on staging (section 1). If set, the team's Jina key. | **yes** |
| `FIRECRAWL_API_KEY` | Leave empty on staging: its 1,000 credits a month are the team's (section 1). | **yes** |
| `REDDIT_SOURCE` | `archive` | no |
| `REDDIT_USER_SALT` | A new random value for staging (`python -c "import secrets; print(secrets.token_hex(32))"`). Never the laptop's. | **yes** |
| `DATABASE_URL` | The Supabase **Session pooler** string, ending in `?sslmode=require` ([database.md](database.md)). The direct connection is IPv6 only. | **yes** |
| `DB_TIMEOUT_SECONDS` | `2` | no |
| `SUPABASE_URL` | The project's address, `https://<project-ref>.supabase.co`, with no slash at the end. The backend checks sign-ins against this project's public signing keys (US-30). Empty turns sign-in off: every caller is a guest. | no |
| `MATCHER` | `model` (see below) | no |
| `MEMORY` | `on`, the default: a signed-in shopper's preferences are remembered (US-06). `off` switches memory off, and the interview runs as before. | no |
| `EXTRACT_MAX_SOURCES` | `5` | no |
| `SEARCH_RUN_WORKERS` | `1` | no |
| `PYTHON_VERSION` | Set by `render.yaml` (`3.11.15`). Nothing to enter. | no |
<!-- render-variables -->

**Why these values**
- **`MATCHER=model`.** DEP-1 tries the trained matcher on staging when it fits the memory (P-10), though the app's own
  default stays fuzzy (ml/MODELS.md, card 1). S3-15 measured a whole search at 254 MB peak with the matcher loaded, and
  86 MB without it. That fits 512 MB.
  - With 0.1 CPU the model will often run past its 3-second share of the match stage. That search matches with fuzzy
    matching instead and logs `match: fuzzy matching ran instead of the model: …`.
  - A single batch of pairs can even outlast the stage's 5-second limit. The runner then logs
    `stage match: TimeoutError; using its fallback`, the fallback still matches with fuzzy matching, and the results
    say a simpler fallback was used.
  - If Render's memory graph comes near 512 MB, or the timeouts show up often, set `MATCHER=fuzzy`.
- **`EXTRACT_MAX_SOURCES=5`.** A search reads at most 5 sources: shorter on 0.1 CPU, with fewer model calls.
- **`SEARCH_RUN_WORKERS=1`.** One search at a time on 512 MB.
- **`DATABASE_URL` is set** so saving can be proved. A search still finishes without it: the health check then says
  `"database": "off"` (unset) or `"down"` (unreachable), and nothing else changes.

### Deploy
- **After creation,** each commit on `main` that changes `backend/` deploys by itself once its CI checks pass.
- **To deploy by hand,** open the service and choose **Manual Deploy > Deploy latest commit**.
- **A changed variable** takes effect after a deploy: save it with **Save, rebuild, and deploy**.

### Check it
```bash
curl -s https://<service>.onrender.com/api/health
# {"status":"ok","schema_version":"…","database":"ok"}
```
- **`"database": "off"`** means `DATABASE_URL` is empty.
- **`"down"`** means the database can't be reached: check that the string is the Session pooler one, with
  `sslmode=require`.

To check CORS for the Vercel address (you should see `access-control-allow-origin` with that address):
```bash
curl -s -o /dev/null -D - -X OPTIONS https://<service>.onrender.com/api/presearch/intake \
  -H "Origin: https://<project>.vercel.app" -H "Access-Control-Request-Method: POST"
```

### Roll back
1. Open the service's **Deploys** page, find the last good deploy and choose **Rollback**.
2. Turn **automatic deploys** back on in **Settings**: a rollback from the dashboard turns them off.
3. Fix the problem on `main` with a pull request. Reverting the bad commit is the quickest fix.

## 3. Frontend on Vercel (Yashkumar)

### What the repository sets
- **`frontend/vite.config.ts`** builds with Nitro. Nitro's preset comes from `frontend/src/lib/deploy/nitro-preset.ts`:
  - **`NITRO_PRESET`**, when set, wins;
  - else **`vercel`** when `VERCEL` is set, giving Vercel functions in `.vercel/output`;
  - else **`node-server`**, giving the plain Node server that `npm start` runs (the laptop demo).
- **`frontend/vercel.json`**:
  - names the framework (`tanstack-start`), so detection can't go wrong in our monorepo;
  - installs with `npm ci` from the lockfile, as CI does (`frontend/.npmrc` keeps `legacy-peer-deps`);
  - builds with `NITRO_PRESET=vercel npm run build`, so the Vercel build never depends on Vercel's system variables
    (step 6 below turns them off).

### Create the project
1. Make sure the change is on `main`.
2. In Vercel, choose **Add New > Project** and import the GitHub repository.
3. Set **Root Directory** to `frontend`. Keep **Include files outside the root directory in the Build Step** on (the
   default): the build reads `shared/contracts/` and `scripts/gen-frontend-types.sh`, outside `frontend/`.
4. The framework shows as **TanStack Start**. Leave the build command, output directory and install command as they
   are: `vercel.json` and the framework set them.
5. **Node.js version: nothing to set.** `frontend/package.json`'s `"engines": {"node": "24.x"}` overrides the
   project setting ([Node.js versions](https://vercel.com/docs/functions/runtimes/node-js/node-js-versions)), so
   Vercel builds on Node 24, the same as CI and every member (P-09, D-65). That meets the TanStack Start guide's "24 or
   later", and Vercel no longer moves to a newer major on its own. A change of Node version is a change to `engines`,
   CI's `node-version`, `scripts/doctor.py`'s `NODE_MAJOR` and `docs/setup.md` together; a test
   (`backend/tests/unit/stack/test_doctor.py`) checks the first three agree.
6. **Turn off Vercel's system variables:** Settings > Environment Variables, untick **Enable access to System
   Environment Variables**. This is a second guard.
   - With it on, Vercel adds `VITE_`-prefixed copies such as `VITE_VERCEL_GIT_COMMIT_AUTHOR_NAME` and
     `VITE_VERCEL_GIT_COMMIT_MESSAGE` to the build
     ([framework variables](https://vercel.com/docs/environment-variables/framework-environment-variables)).
   - The first guard is in the code (S4-19): the frontend reads only the allow-listed keys, each by its name, in one
     place, `frontend/src/lib/config.ts` (`PUBLIC_ENV_KEYS` and `publicEnv()`). A build copies into the browser only
     the values the code reads by name, so other `VITE_` variables stay out of every visitor's JavaScript even if one
     is set. Keep the setting off anyway, in case a later change reads the env whole.
7. Add the variables below, then deploy.

### The variables
| Variable | Production | Preview | Secret? |
|---|---|---|---|
| `VITE_API_MODE` | `live` | `mock` | no |
| `VITE_API_BASE_URL` | The Render address, such as `https://<service>.onrender.com`, with no slash at the end | not needed | no |

- **Only public values, and only these two.** A `VITE_` variable the code reads is copied into the JavaScript every
  visitor downloads, so it must never hold a key. The frontend reads only the keys in `PUBLIC_ENV_KEYS`
  (`frontend/src/lib/config.ts`): these two. A new public key is added there and to this table, never read anywhere
  else. Add no other `VITE_` variable on the project, and keep step 6's setting off.
  - `frontend/src/lib/deploy/deploy-config.test.ts` checks that the source names exactly these keys and never
    reads the env whole.
  - `vite-env-build.test.ts` builds the app with a decoy `VITE_` variable set, and checks that it stays out of the
    build.
- **Previews run in mock mode.** Each preview deployment has its own address, which is not in `FRONTEND_ORIGINS`, so
  the backend would refuse its calls. Mock mode shows the sample data and calls nothing.
- **A changed variable needs a redeploy:** the values are built into the code. **Deployments > … > Redeploy** does it.

### Deploy
Each push to `main` deploys production at once, without waiting for CI (section 4 says what follows), and each pull
request gets a preview. To deploy by hand, choose **Deployments > … > Redeploy** on the latest deployment.

### Check it
1. Open the production address. The home page loads.
2. Open the browser's developer tools (Network). A request goes to the Render address (`/api/presearch/intake`) and
   answers 200, with no CORS error in the Console.
3. If the Console shows a CORS error, `FRONTEND_ORIGINS` on Render doesn't hold this exact address (section 2).

### Roll back
[Instant Rollback](https://vercel.com/docs/instant-rollback) serves an earlier build at once, with no new build. On
Hobby it goes back to the **previous** production deployment only.
1. On the project's overview page, choose **Instant Rollback** on the production deployment tile, check the dialog and
   confirm.
2. Fix the problem on `main` with a pull request.
3. **Undo the rollback.** After a rollback, Vercel stops putting new pushes to `main` live. Choose **Manage** on the
   production tile and promote the fixed deployment.

### Build it locally the way Vercel does
From `frontend/`, `NITRO_PRESET=vercel npm run build` writes `.vercel/output/`, which is git-ignored and not linted.
`npm run build` alone still writes `.output/` for `npm start`.

## 4. Connect the two
1. Once Vercel has given the frontend its production address, set `FRONTEND_ORIGINS` on Render to it and deploy.
2. Set `VITE_API_BASE_URL` on Vercel to the Render address and redeploy (section 3).

Preview deployments get their own addresses, which are not in `FRONTEND_ORIGINS`. That is why previews run in mock
mode.

**The two halves deploy on different signals.** Vercel puts a push to `main` live at once. Render waits until the
commit's CI checks pass, and deploys only commits that change `backend/`. Two things follow:
- **After a contract change,** for the few minutes CI takes, the new frontend talks to the old backend, and live
  answers can fail the frontend's contract check.
- **A commit whose CI fails** still reaches Vercel, but never Render. Roll the frontend back (section 3) until `main`
  is green again.

## 5. Smoke check (DECIDED 5)
Run after both parts are deployed. The budget is D-65's (14 Nov 2026), for one search with **Reddit on**
(`REDDIT_SOURCE=archive`): 12 LLM calls, 6 Serper, 14 Reddit archive, 4 Jina and 2 Firecrawl.
- `research.yaml` caps a search at 6 Serper queries and 14 Reddit archive requests (4 listings, 8 comment reads, 2
  retries).
- `EXTRACT_MAX_SOURCES=5` keeps the LLM calls near 10.
- Jina and Firecrawl are empty on staging (section 1), so they use nothing.

The steps:
1. **Health, timed:** `curl -s -w "\n%{time_total} s\n" https://<service>.onrender.com/api/health` should show
   `"database": "ok"`.
2. **CORS:** the preflight in section 2, twice.
   - From the Vercel address, the answer is 200 and names that address in `access-control-allow-origin`.
   - From any other origin (a made-up `https://not-shopsense.example`), it is refused (400) and names no origin.
3. **One live search** from the deployed page, from the request through the interview to the results. Note:
   - the session ID (`sess_…`) and when it ran;
   - whether results came back and were saved;
   - which matcher ran, from Render's logs:
     - **the model:** no match line;
     - **fuzzy, past the share:** `match: fuzzy matching ran instead of the model: …`;
     - **fuzzy, past the stage's limit:** `stage match: TimeoutError; using its fallback`.
4. **Memory:** Render's free plan doesn't show memory (Application Metrics are for paid plans; only the 512 MB limit
   is shown). Instead, check the service's **Events** and **Logs** for that search's minutes: no "out of memory"
   restart or crash should appear.
5. **A first request after a spin-down:** after 15 or more idle minutes, time the health call again.

**The record.** Run on 18 Nov 2026 (19 Nov in India) against Pal's Render service and Yashkumar's Vercel project.
The repository is public, so their addresses are not written here (D-65): `<service>` and `<project>` stand for
them. Times are UTC.

| Date | Check | Result |
|---|---|---|
| 18 Nov 2026, 18:36 | Health, the first call after the service had been idle | HTTP 200 in **28.8 s**, `"database": "ok"`, schema 1.9.0. Most likely the service starting again; how long it had been idle wasn't measured. |
| 18 Nov 2026, 18:37 | Health, warm | HTTP 200 in 0.53 s |
| 18 Nov 2026, 18:37 | CORS from the Vercel address | Preflight 200, `access-control-allow-origin: https://<project>.vercel.app`. A GET carries the same header. |
| 18 Nov 2026, 18:37 | CORS from another origin | Preflight **400**, no `access-control-allow-origin`. A plain GET still answers 200, but with no allow header, so a browser on that origin can't read it. |
| 18 Nov 2026, 18:37–18:39 | The live search's request, criteria and interview, from the deployed page | A made-up request ("wireless earbuds for the gym under 3000 rupees"). <ul><li>**Intake:** the keyword rules (`detected_by: rules`): the page said "Our backup reader filled this in".</li><li>**Criteria:** generated, 10 of them.</li><li>**Interview:** 10 answers.</li><li>**The request:** saved (`request_saved: saved`).</li></ul> |
| 18 Nov 2026, 18:39:14–18:39:56 | The live search (`srch_a53470eeb7c65908a51da418`) | Finished in **41.7 s**, not partial, **saved** (`saved: true`). <ul><li>**Stages:**<ul><li>research OK (24.2 s, 54 sources: 20 Reddit, 20 search snippets, 6 review sites, 5 YouTube, 3 stores);</li><li>extract reported a time-out (16.6 s): at least 2 sources ran out of time and used the fallback;</li><li>match, verify, enrich, decide and explain OK;</li><li>commerce skipped (nothing ranked).</li></ul></li><li>**`llm_unavailable: true`:** no source sent to the AI services got an answer: each failed on every provider or ran out of time. So no claim was read and nothing was ranked. The page showed "We couldn't reach the AI services that read the sources" and the coverage note.</li><li>**Pending from Pal:** Render's logs for 18:37–18:40, to show why intake fell back and why the extraction calls failed, and whether the matcher model loaded. If there is no `match: fuzzy matching ran instead of the model` line, it loaded.</li></ul> |
| 18 Nov 2026, 18:37–18:40 | Calls used against the budget | Not countable: staging's counts and the providers' calls are not visible from outside. <ul><li>**Serper and Reddit:** bounded by `research.yaml`'s per-search caps, 6 and 14, the budget's numbers.</li><li>**LLM:** unknown. A failed read may try several providers, so the 12 can't be confirmed.</li><li>**Jina and Firecrawl:** use nothing if their keys are empty on the service, as section 1 asks; not checked from here.</li></ul> |
| 18 Nov 2026, 18:36–18:42 | Memory | Not measurable on the free plan (only the 512 MB limit is shown). Whether Events or Logs show an out-of-memory restart in these minutes: pending from Pal. The search finished, which a restart during it would have stopped. Match had no names to score (it took 0 ms), so this search tested at most the matcher model loading, not its scoring on 0.1 CPU (P-10). |
| 18 Nov 2026, 19:12 | Health after 31 minutes without our calls | HTTP 200 in 0.59 s: the service had not spun down. The first call above (28.8 s) is the start-up time we saw. |

## 6. Staging data to delete
Every session the team's own checks save on staging is listed here, so Om can delete it from Supabase.

Staging can't list the searches of anyone else who has the address: those are saved too, with the request they typed.
**D-65 (14 Nov 2026) settled this:**
- staging keeps the team's database (`DATABASE_URL`);
- the address stays within the team until login (US-30);
- the sessions the team's own checks create are listed here and deleted afterwards.

| Session ID | Created | What it holds | Deleted |
|---|---|---|---|
| `sess_fe23922a88fbcae0d852262d` | 18 Nov 2026, 18:37 UTC (the smoke check, section 5) | One request (18:37) and one search, `srch_a53470eeb7c65908a51da418` (18:39); no user linked | 18 Nov 2026, by Om, after listing them read-only. None are left for this session. |

**Not yet accounted for:** 11 requests and 5 searches created since 17 Nov 2026, 18:06 UTC (23:36 IST), that no
hand-over lists. They are probably the team's own searches, on staging or from a laptop. Rows are deleted only by exact
session ID, so they stay until their owners are known: anyone who wants theirs deleted sends Om the session IDs.
