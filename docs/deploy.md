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
    - share the address only within the team.
- **512 MB is shared by every search running at once**, so staging runs one search at a time (`SEARCH_RUN_WORKERS=1`).
  A second search gets the app's "busy, try again" answer.
- **Every search is saved** (D-36), and staging saves into the team's Supabase. A stranger's search would save the
  request they typed, which is private data (D-51). Keep the address within the team (section 6).

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
| `SERPER_API_KEY` | The team's Serper key | **yes** |
| `JINA_API_KEY` | Leave empty on staging (section 1). If set, the team's Jina key. | **yes** |
| `FIRECRAWL_API_KEY` | Leave empty on staging: its 1,000 credits a month are the team's (section 1). | **yes** |
| `REDDIT_SOURCE` | `archive` | no |
| `REDDIT_USER_SALT` | A new random value for staging (`python -c "import secrets; print(secrets.token_hex(32))"`). Never the laptop's. | **yes** |
| `DATABASE_URL` | The Supabase **Session pooler** string, ending in `?sslmode=require` ([database.md](database.md)). The direct connection is IPv6 only. | **yes** |
| `DB_TIMEOUT_SECONDS` | `2` | no |
| `MATCHER` | `model` (see below) | no |
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
5. **Node.js version: nothing to set.** `frontend/package.json`'s `"engines": {"node": ">=20.19"}` overrides the
   project setting ([Node.js versions](https://vercel.com/docs/functions/runtimes/node-js/node-js-versions)). Vercel
   then builds with its newest major, 26.x today, which meets the TanStack Start guide's "24 or later". It moves to a
   newer major on its own; pin `engines` if that ever breaks the build.
6. **Turn off Vercel's system variables:** Settings > Environment Variables, untick **Enable access to System
   Environment Variables**.
   - With it on, Vercel adds `VITE_`-prefixed copies such as `VITE_VERCEL_GIT_COMMIT_AUTHOR_NAME` and
     `VITE_VERCEL_GIT_COMMIT_MESSAGE` to the build
     ([framework variables](https://vercel.com/docs/environment-variables/framework-environment-variables)).
   - `src/lib/api/client.ts` reads `import.meta.env` whole, so the build would copy all of them into every visitor's
     JavaScript.
7. Add the variables below, then deploy.

### The variables
| Variable | Production | Preview | Secret? |
|---|---|---|---|
| `VITE_API_MODE` | `live` | `mock` | no |
| `VITE_API_BASE_URL` | The Render address, such as `https://<service>.onrender.com`, with no slash at the end | not needed | no |

- **Only public values, and only these two.** A `VITE_` variable is copied into the JavaScript every visitor
  downloads, so it must never hold a key. The build copies *every* `VITE_` variable set on the project, not just the
  ones the code names. So add no other `VITE_` variable, and keep step 6's setting off.
  `frontend/src/lib/deploy/deploy-config.test.ts` checks that the source names only these two.
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
Run after both parts are deployed. DEP-1's budget is 12 LLM calls, 6 Serper calls and 10 Reddit archive calls, for one
search.
- **Serper and LLM fit.** `research.yaml` caps a search at 6 Serper queries, and `EXTRACT_MAX_SOURCES=5` keeps the LLM
  calls near 10.
- **Reddit doesn't.** A search may use up to 14 Reddit archive requests (4 listings, 8 comment reads, 2 retries), more
  than the budget. So for the smoke search set `REDDIT_SOURCE=off` on Render, unless Om raises the Reddit budget to 14,
  and put it back afterwards.

The steps:
1. **Health, timed:** `curl -s -w "\n%{time_total} s\n" https://<service>.onrender.com/api/health` should show
   `"database": "ok"`.
2. **One live search** from the deployed page, from the request through the interview to the results. Note:
   - the session ID (`sess_…`) and when it ran;
   - whether results came back and were saved;
   - which matcher ran, from Render's logs:
     - **the model:** no match line;
     - **fuzzy, past the share:** `match: fuzzy matching ran instead of the model: …`;
     - **fuzzy, past the stage's limit:** `stage match: TimeoutError; using its fallback`.
3. **Memory:** the backend's peak memory during that search, from the service's **Metrics > Memory** graph.
4. **A first request after a spin-down:** after 15 or more idle minutes, time the health call again.

| Date | Check | Result |
|---|---|---|
| — | Not run yet: the staging services were not created on 14 Nov 2026. | — |

## 6. Staging data to delete
Every session the team's own checks save on staging is listed here, so Om can delete it from Supabase.

Staging can't list the searches of anyone else who has the address: those are saved too, with the request they typed.
That is why the address stays within the team. **Open for Om (D-51, D-36):** whether staging should save into its own
database instead, and how rows from outside the team would be found and deleted.

| Session ID | Created | Deleted |
|---|---|---|
| — | — | — |
