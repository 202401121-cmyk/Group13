# API setup (Sprint 1)

Owner: whoever holds the "sign up and smoke-test every API" task in Sprint 1.
Goal: every key works, the real free-tier limits are written down, and the terms are checked
**before** anyone builds on a source.

Free tiers change without notice. The numbers in the plan were correct in August 2026; record
what you actually see in the table at the bottom and post it in `#dev`.

## Rules for keys

- Keys go only in your local `.env` and in GitHub → Settings → Secrets. Never in code, issues or Slack.
- Each member creates their **own** keys for local work, so one person's usage doesn't exhaust everyone's quota.
- CI never uses real keys: tests run on recorded responses.

## Services

| # | Service | Env var(s) | Where to get it | Check during the smoke test |
|---|---|---|---|---|
| 1 | Groq | `GROQ_API_KEY` | console.groq.com → API Keys | Requests/min, requests/day, **tokens/day** (the tight one); the models available free |
| 2 | Google Gemini | `GEMINI_API_KEY` | aistudio.google.com → Get API key | Your actual limits in AI Studio; the free-tier data-use terms. **Public text only** (trains on inputs) |
| 3 | OpenRouter | `OPENROUTER_API_KEY` | openrouter.ai → Keys | Current `:free` models and daily cap. **Public text only** (training unconfirmed) |
| 4 | Cerebras | `CEREBRAS_API_KEY` | cloud.cerebras.ai | Requests/min, tokens/day, **context window on the free tier** |
| 4a | Mistral | `MISTRAL_API_KEY` (and `MISTRAL_MODEL`, `MISTRAL_RPS`, `MISTRAL_TPM`, `MISTRAL_DAILY_TOKENS`) | console.mistral.ai → API Keys | Admin Panel → API → Limits: requests/s, tokens/min, **tokens/month**. **Public text only** (the free plan trains on inputs) |
| 5 | Serper | `SERPER_API_KEY` | serper.dev | Free queries: monthly or one-off? Shopping results available for India? |
| 6 | Reddit | none for the archives; `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` only for Reddit's own API | Archives: no sign-up. Reddit's API: request access, then reddit.com/prefs/apps → "script" app | See "Reddit (US-12)" below: archives first, Reddit's API only once access is approved |
| 7 | YouTube Data API | `YOUTUBE_API_KEY` | console.cloud.google.com → enable "YouTube Data API v3" → Credentials | Daily units (search costs ~100). **Terms for transcripts**: if a transcript library isn't allowed, US-38 uses titles, descriptions and comments only |
| 8 | Jina Reader | `JINA_API_KEY` (optional) | jina.ai/reader | Rate limit with and without a key |
| 9 | Supabase | `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `DATABASE_URL` | supabase.com → New project (region: Mumbai) | pgvector extension can be enabled; Google sign-in provider configured; free project pause rules |
| 10 | Hugging Face | none needed to download public models | huggingface.co | Models download on a laptop: MiniLM / bge-small, an NLI model |
| 11 | Google Colab | none | colab.research.google.com | A GPU runtime starts; checkpoints save to Drive |

Supabase is one shared project owned by the team (Om as admin), not one per person.
Only the backend ever sees the service-role key.

## LLM routing (US-46)

Every LLM call says two things, and `backend/config/models.yaml` turns them into the providers to try, in order.

- **Data class**: what the prompt carries. Pick the most private thing in it.
  - `private`: the shopper's own words or preferences (their request, reply, use case, questions).
  - `reddit`: posts and comments from Reddit.
  - `public`: our own fixed text (prompts, category labels from our lexicon) or public web pages.
- **Difficulty**: how big the job is. `short` (small prompt and answer), `medium` (a longer answer, such as an
  explanation), `long` (a long input, such as many pages or comments).

**The one rule (build rule 8):** private and Reddit text only go to providers whose terms say they do not train
on what we send. The loader refuses a `routes` entry that breaks this, and the wrapper checks it again on every
call. If no allowed provider is left, the call raises `LLMUnavailable` and the stage uses its own fallback (for
intake, the keyword rules; for criteria, the saved or fixed criteria set). Reddit text is never used to train or
fine-tune any model of ours either.

### Routes (`routes:` in models.yaml)

| Data class | short | medium | long |
|---|---|---|---|
| private | groq, cerebras | groq, cerebras | groq, cerebras |
| reddit | groq, cerebras | groq, cerebras | groq, cerebras |
| public | mistral, groq | mistral, gemini, groq, openrouter | gemini, mistral, cerebras, openrouter |

Groq's free tier has a small daily token allowance, so public work goes to Mistral and Gemini first and Groq is
kept for private text. Long public inputs start on Gemini for its 1M-token window.

### Does the provider train on our inputs? (`trains_on_inputs`, read 3 Nov 2026)

| Provider | Trains? | Source (the `terms_url` in models.yaml) |
|---|---|---|
| Groq | no | console.groq.com/docs/your-data: does not keep inference data by default; the services agreement (4.2) does not let Groq train on inputs or outputs |
| Cerebras | no | cerebras.ai/terms-of-service: no right to use our content for training; the privacy policy (6) says inputs and outputs are not kept |
| Gemini (free) | **yes** | aistudio.google.com/docs/terms, unpaid services: prompts may be used to improve Google's products and machine learning, and people may read them |
| Mistral (free plan) | **yes** | help.mistral.ai/en/articles/347617: on the free plan inputs and outputs may be used for training. An opt-out exists, but we cannot confirm it for our key |
| OpenRouter (`:free`) | **yes** | openrouter.ai/docs/features/privacy-and-logging: it depends on the model's host, and we cannot confirm it for ours |

When a provider's terms change, update its flag, `terms_url` and `terms_checked` together. A model's
`trains_on_free_tier` must match its provider's flag, or the app will not start.

### Call sites

| Call site | Task | Prompt carries | Class | Difficulty |
|---|---|---|---|---|
| `app/intake/stage.py` | `detect_intent` | the shopper's request and their reply to our question | private | short |
| `app/criteria/stage.py` | `generate_criteria` | category label and the shopper's use case | private | short |
| `app/criteria/stage.py` | `generate_criteria` | only a category label from our lexicon (no use case) | public | short |

`detect_intent`, `classify_reply`, `answer_user_question`, `write_question` and `write_explanation` always carry
the shopper's words, so the wrapper refuses any class but private for them (`PERSONAL_TASKS`). A new call site
must pass `data_class=` and `difficulty=`, and be added to this table and to
`backend/tests/unit/llm/test_call_sites.py`.

### Mistral

- Model: `MISTRAL_MODEL`, default `ministral-8b-latest` (Ministral 3 8B, 256k-token window). On 3 Nov 2026 it
  answered our JSON check; `mistral-small-latest` answered HTTP 429 with a limit of 0 requests a minute on our key.
- JSON mode is on (`response_format: {"type": "json_object"}`). Replies are still checked against the schema,
  and invalid JSON is retried once, then the next provider is tried.
- Our limits: `MISTRAL_RPS=2` requests a second and `MISTRAL_TPM=500000` tokens a minute, shared by the whole
  app. The API reported 188 requests a minute and 625,000 tokens a minute for this model on 3 Nov 2026.
- Daily budget: `MISTRAL_DAILY_TOKENS=1000000`. Mistral shows its monthly allowance only in its Admin Panel →
  API → Limits, so this is a cautious guess: set it to that allowance ÷ 31, less 10%. The count is kept in
  memory, per UTC day, and starts again at zero when the app restarts.

### When a provider fails

- HTTP 429: skipped for as long as it asks (Retry-After or a reset header), held between 60 and 120 seconds.
- Three timeouts or server errors in a row: skipped for 90 seconds, then one trial call.
- HTTP 401, 402 or 403 (bad key, or the account needs a plan): skipped for the rest of the run.
- Over its daily budget: not sent, logged with outcome `skipped_budget`.
- Every attempt writes one call record: task, data class, provider, model, tokens, time and outcome. Never the
  prompt or the reply.

## Smoke test

For each LLM provider: one tiny call returning JSON, then read the rate-limit response headers.
For each data source: one real request, saved to `backend/tests/fixtures/<source>/` as the first
recorded response (remove anything personal first). These recordings are what the tests use.

### Run the LLM smoke test

With your LLM keys in `.env`, run from `backend/`:

```bash
python -m scripts.smoke_llm
```

It sends one tiny JSON request (fixed public text) to each provider that has a key, Mistral included, through
the same adapter and settings the app uses, and prints a table: provider, model, whether it trains on inputs,
works (yes/no/skipped), latency, tokens used, and the provider's rate-limit response headers. Then it prints our
own limits (Mistral's requests a second, tokens a minute and tokens a day). It never prints a key, calls real
APIs (so it never runs in CI), and always exits 0.

Then copy what you saw into the results table below and into `backend/config/models.yaml` (model name,
context window, output limit), and remove the `# UNVERIFIED` comment above each row you confirmed.

### Run the Reddit archive smoke test

No key is needed. From `backend/`:

```bash
python -m scripts.smoke_sources                               # one request per archive
python -m scripts.smoke_sources --sample "laptop for coding"   # also a real search, 4 requests per archive
```

It asks each archive for the newest post in r/laptops, through the same HTTP layer the app uses, and prints
whether it works, the latency and the date of that post (how far behind the archive is). `--sample` also runs the
app's own thread search and comment read for the laptop subreddits and prints counts, dates and permalinks. It
never prints a username, never touches the app's cache, and always exits 0. The live tests do the same check as a
pass or fail: `RUN_LIVE_TESTS=1 pytest -m live tests/live -q` (never in CI).

## Reddit (US-12)

Reddit threads are community evidence: what owners say after weeks of use. We read them through public archives,
not by scraping reddit.com.

| Source | When it is used | Freshness | Our limits |
|---|---|---|---|
| Arctic Shift (`ARCTIC_SHIFT_BASE_URL`) | Always first (`REDDIT_SOURCE=archive`, the default) | A few days behind; comments arrive a day or two after their thread | 1 request a second for the whole app; on 429 or 422, wait for `X-RateLimit-Reset` (at most 30 s) and retry once; paused 10 minutes after 3 failures |
| PullPush (`PULLPUSH_BASE_URL`) | Only if Arctic Shift failed or found nothing | Stopped around May 2025: results say "Reddit via older archive (to May 2025)" | 1 request every 2 s; one retry; paused 30 s after 3 failures |
| Reddit's own API (PRAW, read-only) | Only with `REDDIT_SOURCE=official` and all three `REDDIT_*` API settings | Live | PRAW's own rate limiting, plus our budget and breaker |

Every request has a 10 s timeout and the User-Agent `ShopSense/<version> (IT314 student project;
+https://github.com/202401121-cmyk/Group13)`. One search uses about 10 requests at most (`RequestBudget`): one per
subreddit (up to 4), the rest for comment threads. Cleaned results are cached for 24 hours in memory and in the
git-ignored `backend/.cache/reddit/`; a failure is never cached.

**Rules (never break these):**

- No scraping of reddit.com pages, and no keyless `.json` or RSS endpoints. Pushshift is not used: its access is
  for moderators doing community moderation only.
- Usernames are never stored, logged or shown. Each Source keeps only `author_hash`, a salted sha256 of the name
  (`REDDIT_USER_SALT`), so US-13 can count distinct people. Without a salt no hash is kept at all.
- Deleted, removed, over-18 and AutoModerator posts and comments are skipped.
- Reddit text is data, never instructions (build rule 3). It goes only to LLM providers that do not train on
  inputs (the `reddit` data class, US-46), and it is never used to train or fine-tune any model of ours.
- Every Source keeps its canonical `https://www.reddit.com/r/<sub>/comments/<id>/` permalink (built from ids, not
  copied), the time it was fetched, and `source_detail` (`arctic_shift`, `pullpush` or `official`).

**How threads are found.** Arctic Shift's keyword search (`title`, `selftext`, `query`) is "not supported with
very active subreddits": on 3 Nov 2026 it answered 422 at once for r/SuggestALaptop and r/laptops. So we ask for
each subreddit's newest 100 posts in a date window and match the query's words ourselves; one cached listing
serves every query. The window ends 2 days before today, because the archive has a thread's comments only a day
or two after it is posted (threads from 31 Oct had theirs on 3 Nov; most from 1-2 Nov had none).

**Which subreddits.** `backend/config/reddit_subreddits.yaml` lists them for each of the 49 categories, plus shared
India-wide and buy-it-for-life lists by product family. Every name was checked on Arctic Shift on 3 Nov 2026; six
stale ones were dropped. Add a subreddit only after the same check, and tell the US-12 owner.

**PullPush is unconfirmed.** Its documentation could not be read on 3 Nov 2026 (`pullpush.io` showed a Cloudflare
challenge, `api.pullpush.io` answered HTTP 522 and later timed out). The code follows the Pushshift-style API it
copies (`/reddit/search/submission/`, `/reddit/search/comment/`, with `q`, `subreddit`, `size`, `sort`,
`sort_type`, `link_id`) and reads replies leniently. Check it with the smoke test when it is back.

**Reddit's own API.** Access for a student project needs Reddit's approval. Access requested on: `<date>` (fill
in), by `<name>`. Until it is approved, leave `REDDIT_SOURCE=archive`. When it is: create a "script" app at
reddit.com/prefs/apps, set the three `REDDIT_*` API settings and `REDDIT_SOURCE=official`. App-only, read-only
sign-in: no Reddit password is ever used.

**Open question (for Om).** We keep a quote's source for evidence. If a thread or comment is deleted on Reddit
after we read it, should evidence we have kept for longer than the 24-hour cache be dropped too? Today nothing is
stored past 24 hours, so the question matters once US-30 saves evidence to the database.

### Settings

| Variable | Default | Meaning |
|---|---|---|
| `REDDIT_SOURCE` | `archive` | `archive`, `official` or `off` (no Reddit at all) |
| `REDDIT_ARCHIVES` | `arctic_shift,pullpush` | The archives to try, in order |
| `ARCTIC_SHIFT_BASE_URL` | `https://arctic-shift.photon-reddit.com` | https only |
| `PULLPUSH_BASE_URL` | `https://api.pullpush.io` | https only |
| `REDDIT_USER_SALT` | none | Secret salt for `author_hash`. Make one with `python -c "import secrets; print(secrets.token_hex(16))"`; changing it makes every hash new |
| `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` | none | Reddit's own API, only for `REDDIT_SOURCE=official` |

A bad value is logged by name (never its value) and the default is used, so a typo never stops a search.

## Results table (fill in, then post in `#dev`)

| Service | Works? | Limits seen | Context window (LLMs) | Terms OK? | Notes |
|---|---|---|---|---|---|
| Groq | | | | | |
| Gemini | | | | | |
| OpenRouter | | | | | |
| Cerebras | | | | | |
| Mistral | yes (3 Nov 2026, `ministral-8b-latest`) | 188 req/min, 625,000 tokens/min (response headers); monthly allowance: see Admin Panel | 262,144 | free plan trains on inputs: public text only | `mistral-small-latest` gave 429 with a 0 req/min limit |
| Serper | | | — | | |
| Reddit (Arctic Shift) | yes (3 Nov 2026) | about 1 request a second; dynamic limits; keyword search refused on busy subreddits | — | public archive, no key; read-only, no usernames kept | newest post 3 Nov 2026 09:22 UTC; a laptop search took about 3 s for 2 subreddits |
| Reddit (PullPush) | no (3 Nov 2026: HTTP 522, then timeouts) | — | — | docs unreadable (Cloudflare) | data stops around May 2025 |
| YouTube | | | — | | |
| Jina Reader | | | — | | |
| Supabase | | | — | | |

The LLM rows become `backend/config/models.yaml` (US-26): one entry per model with its context window,
output limit, and whether the free tier trains on prompts. Each provider's "trains on inputs" flag, with the
terms page it came from, decides which routes it may appear on (US-46, "LLM routing" above).
