# API setup

Every outside service ShopSense calls: what it is for, how to get a key, the free limits, what its terms allow,
and what happens when it fails. Owner: Jenish Kavad. Terms and limits last checked on 3 November 2026.

Free tiers and terms change without notice. Before building on a service, run the smoke test below and re-read
its terms, then update this page.

## Rules for keys

- Keys go only in your own root `.env` (copy `.env.example`; there is no `backend/.env`). Never in code, issues,
  chat or screenshots.
- Each member makes their own keys for local work, so one person's use doesn't use up everyone's quota. The keys
  on the demo laptop are in Om's root `.env`.
- Take the least access a service offers: a read-only (or fine-grained read) Hugging Face token, and a YouTube key
  restricted to the YouTube Data API v3.
- CI never uses real keys: tests run on recorded responses and fakes, and live tests run only with
  `RUN_LIVE_TESTS=1`.

## Services

| Service | What ShopSense uses it for | Env var(s) | Terms verdict (3 Nov 2026) | Smoke test (3 Nov 2026) |
|---|---|---|---|---|
| [Groq](#groq) | LLM for private, Reddit and public text | `GROQ_API_KEY` | OK with limits | ok |
| [Gemini](#gemini) | LLM for public text only | `GEMINI_API_KEY` | OK as planned | ok |
| [OpenRouter](#openrouter) | LLM for public text only, last choice | `OPENROUTER_API_KEY` | OK as planned | ok |
| [Cerebras](#cerebras) | LLM for private, Reddit and public text | `CEREBRAS_API_KEY` | **Not OK**: no free tier | failed (HTTP 402) |
| [Mistral](#mistral) | LLM for public text only | `MISTRAL_API_KEY` (+ 4 settings) | OK as planned | ok |
| [Serper](#serper) | Web search results with links | `SERPER_API_KEY` | OK with limits | ok |
| [Reddit](#reddits-own-api) | Community evidence | archives: none; own API: `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` | Own API: **not OK** (closed to us); archives in use | skipped (no keys) |
| [YouTube](#youtube) | Video titles, descriptions, comments | `YOUTUBE_API_KEY` | OK with limits (no transcripts) | ok |
| [Jina Reader](#jina-reader) | Reads public web pages as text | `JINA_API_KEY` (optional) | OK with limits | ok |
| [Hugging Face](#hugging-face) | Downloads public models (ML track) | `HF_TOKEN` (optional) | OK as planned | skipped (no token) |
| Supabase | The database (optional) | `DATABASE_URL`, `DB_TIMEOUT_SECONDS` | see [database.md](database.md) | checked by `GET /api/health` |
| Google Colab | GPU notebooks (ML track) | none | not checked | not part of the smoke test |

## Smoke test

### One command for every service

From `backend/`, with your keys in the root `.env`:

```bash
python -m scripts.smoke_apis                       # every configured service except Serper
python -m scripts.smoke_apis --only groq,youtube   # just these services
python -m scripts.smoke_apis --include serper      # also Serper: each run uses one of its 2,500 free queries
```

Each service gets the cheapest call that proves its key works:

- **LLMs** (Groq, Gemini, OpenRouter, Cerebras, Mistral): one call through the app's own provider client and
  `backend/config/models.yaml`, with the fixed public prompt "Reply with the single word OK." and 5 output tokens.
  It passes if the reply contains OK. Reasoning models (gpt-oss, nemotron) can spend 5 tokens thinking and send no
  text; only then is the call repeated once with a 200-token cap, and the note says so.
- **Serper:** one search, only with `--include serper`.
- **YouTube:** `videos.list` for one public video (1 quota unit). Never `search.list`.
- **Reddit's own API:** only when `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET` are set: an app-only token, then one
  listing with `limit=1`, with the User-Agent Reddit requires. Never the keyless `.json` or RSS feeds.
- **Jina Reader:** reads `https://example.com` (with `JINA_API_KEY` if set; it also works without one).
- **Hugging Face:** `whoami`, which also shows whether the token is read-only.

A service with no key is "skipped (not configured)", never a failure. Each service gets at most 20 seconds, and one
failing never stops the others. It prints a table (service, status, latency, note) and any rate-limit headers, and
exits 0 if every configured service passed, 1 if any failed. It never prints a key, and the prompt is fixed public
text, so it is safe for every provider, including those that train on inputs. The tests run offline in CI; the
real run is marked `live` (`RUN_LIVE_TESTS=1 pytest -m live tests/live -q`).

### Last run: 3 November 2026

`python -m scripts.smoke_apis --include serper` on the demo laptop (Windows 11), then the LLM rows again after a
fix to the reasoning-model retry:

| Service | Status | Latency | Note |
|---|---|---|---|
| Groq | ok | 922 ms | `openai/gpt-oss-120b`, needed the 200-token retry (it reasons first). Headers: 1,000 requests a day, 8,000 tokens a minute |
| Gemini | ok | 1,625 ms | `gemini-3.5-flash`. No rate-limit headers sent |
| OpenRouter | ok | 3,797 ms | `nvidia/nemotron-3.5-lightning:free`, needed the 200-token retry |
| Cerebras | failed | 422 ms | HTTP 402: the account needs a plan or credits |
| Mistral | ok | 1,031 ms | `ministral-8b-latest`. Headers: 188 requests and 625,000 tokens a minute |
| Serper | ok | 2,406 ms | 9 results, 1 credit used |
| Reddit | skipped | - | no `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET` |
| YouTube | ok | 188 ms | `videos.list`, 1 quota unit |
| Jina Reader | ok | 2,797 ms | read example.com with `JINA_API_KEY` |
| Hugging Face | skipped | - | no `HF_TOKEN` |

Exit code 1, because of Cerebras.

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

## Each service

Free limits are as the provider's own page states them, on the date read; the smoke test row says what our key
actually saw. Quotes are copied from the provider's page.

### Groq

- **Used for:** LLM calls with private, Reddit and public text. It is first for private text.
- **Sign up:** [console.groq.com](https://console.groq.com) → API Keys. **Env:** `GROQ_API_KEY`.
- **Free limits** ([rate limits](https://console.groq.com/docs/rate-limits), 3 Nov 2026): `openai/gpt-oss-120b` 30
  requests a minute, 1,000 a day, 8,000 tokens a minute, 200,000 tokens a day. The page calls these "the base limits
  for the Developer plan" next to a Free plan tab, so treat them as the most we get. Smoke test: the headers said
  1,000 requests and 8,000 tokens, which matches.
- **Terms** ([services agreement](https://console.groq.com/docs/legal/services-agreement), last modified 22 Jun
  2026; [your data](https://console.groq.com/docs/your-data)): "Groq is not permitted to use Inputs or Outputs for
  training or fine-tuning" (section 4.2), and "By default, Groq does not retain customer data for inference
  requests." Reliability and abuse logs may be kept for up to 30 days; Zero Data Retention can be switched on in
  Data Controls. It is for developers building apps; no reselling.
- **Verdict: OK with these limits:** about 1,000 requests a day. Switching on Zero Data Retention is a team
  decision.
- **If it fails:** private and Reddit text has no other working provider while Cerebras is out (below), so the
  stage uses its own fallback: keyword rules for intake, fixed criteria sets for criteria.

### Gemini

- **Used for:** LLM calls with public text only. It is first for long public inputs (1M-token window).
- **Sign up:** [aistudio.google.com](https://aistudio.google.com) → Get API key. **Env:** `GEMINI_API_KEY`.
- **Free limits** ([rate limits](https://aistudio.google.com/docs/rate-limits), 3 Nov 2026): not published; the page
  says they can be viewed in Google AI Studio. Daily quotas reset at midnight Pacific time. Smoke test: ok; Gemini
  sends no rate-limit headers.
- **Terms** ([Gemini API terms](https://aistudio.google.com/docs/terms), effective 23 Mar 2026): on the unpaid tier,
  what we send is used to improve Google's products, and "human reviewers may read, annotate, and process your API
  input and output." Also: "Do not submit sensitive, confidential, or personal information to the Unpaid Services."
- **Verdict: OK as planned:** public text only (`trains_on_inputs: true`).
- **If it fails:** the next provider in the public route. The free tier sometimes answers HTTP 503 when busy.

### OpenRouter

- **Used for:** LLM calls with public text only, as the last choice.
- **Sign up:** [openrouter.ai](https://openrouter.ai) → Keys. **Env:** `OPENROUTER_API_KEY`.
- **Free limits** ([limits](https://openrouter.ai/docs/api-reference/limits), 3 Nov 2026): 20 requests a minute on
  `:free` models; 50 a day until the account has bought at least 10 credits, then 1,000 a day. Smoke test: ok, in
  about 4 s.
- **Terms** ([privacy and logging](https://openrouter.ai/docs/features/privacy-and-logging)): whether prompts are
  used for training depends on the model's host. OpenRouter says prompts "will not be trained on, but there are
  exceptions." Hosts that train can be blocked for the account or per request; we have not confirmed which host
  serves our model.
- **Verdict: OK as planned:** public text only, about 50 requests a day.
- **If it fails:** it is last in every public route, so the stage uses its own fallback.

### Cerebras

- **Used for:** LLM calls with private, Reddit and public text, second after Groq, once it works.
- **Sign up:** [cloud.cerebras.ai](https://cloud.cerebras.ai). **Env:** `CEREBRAS_API_KEY`.
- **Free limits** ([rate limits](https://inference-docs.cerebras.ai/support/rate-limits),
  [pricing](https://cerebras.ai/pricing), 3 Nov 2026): Cerebras "doesn't currently offer a no-cost tier that renews
  automatically". A new account gets a one-time $5 credit, valid 30 days, after adding a verified payment method;
  during it, `gpt-oss-120b` allows 5 requests a minute and 1M tokens a day. Smoke test: HTTP 402, the account needs
  a plan or credits.
- **Terms** ([terms of service](https://cerebras.ai/terms-of-service), [privacy policy](https://cerebras.ai/privacy-policy),
  both 27 Aug 2024): Cerebras gets no right to use our content "for the purpose of training or fine-tuning models",
  and "We do not retain inputs and outputs."
- **Verdict: Not OK: fallback is Groq alone** for private and Reddit text, then each stage's own fallback. Its data
  terms are fine; the problem is cost. Whether to add a card for the trial credit is a team decision for Om.
- **If it fails:** the wrapper marks it dead for the run after the first 402 and moves on.

### Mistral

- **Used for:** LLM calls with public text only. It is first for short and medium public jobs.
- **Sign up:** [console.mistral.ai](https://console.mistral.ai) → API Keys. **Env:** `MISTRAL_API_KEY`, plus
  `MISTRAL_MODEL`, `MISTRAL_RPS`, `MISTRAL_TPM` and `MISTRAL_DAILY_TOKENS` (see "Mistral" under LLM routing).
- **Free limits** ([plan tiers](https://docs.mistral.ai/admin/user-management-finops/tier), 3 Nov 2026): shown only in
  the Admin Panel → API → Limits. Smoke test headers: 188 requests and 625,000 tokens a minute for
  `ministral-8b-latest`.
- **Terms** ([help article](https://help.mistral.ai/en/articles/347617)): on the free plan "we may use your data
  (input and output)" for training. An opt-out exists in the admin console's Privacy page, and "Vibe and API opt-out
  toggles are separate."
- **Verdict: OK as planned:** public text only, because we cannot prove the opt-out is set for our key.
- **If it fails:** the next provider in the public route.

### Serper

- **Used for:** web search results that link to review sites, Reddit threads and videos (US-12).
- **Sign up:** [serper.dev](https://serper.dev), no card needed. **Env:** `SERPER_API_KEY`.
- **Free limits** ([serper.dev](https://serper.dev), 3 Nov 2026): 2,500 free queries in total, once, not each month.
  Smoke test: one search, 9 results, 1 credit used.
- **Terms** ([terms](https://serper.dev/terms), updated 29 May 2024): "Serper is a business-to-business service and
  does not provide end-user (consumer) services." Do not "Mirror the materials on any other server as-is with
  no-value-added", do not misrepresent where results come from, use one account, and don't get around the limits.
- **Verdict: OK with these limits:** 2,500 queries in total, so cache searches for 24 hours (build rule 7) and show
  results only inside our own analysis, with their links.
- **If it fails or runs out:** research carries on with the Reddit archives and the page reader, and
  `coverage_note` says web search was unavailable.

### Reddit's own API

- **Used for:** nothing yet. Reddit evidence comes from the archives (see "Reddit (US-12)" below), which need no key.
- **Env:** `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT`, only with `REDDIT_SOURCE=official`.
- **Access** (3 Nov 2026): Reddit's own policy and help pages could not be opened from our tools (HTTP 403), so
  none of this is confirmed from Reddit itself yet. Reddit requires approval before any API use. Self-service keys
  reportedly ended on 11 November 2025. A summary of Reddit's 8 October 2026 developer changelog says new public
  Data API access requests closed on 31 October 2026, and the remaining public access ends during 2027. **Open:**
  Jenish opens Reddit's Data API terms and that changelog in a normal browser and records here whether a request
  can still be sent, and the date it was sent.
- **Rate limit and User-Agent** (from the same summary and Reddit's archived API wiki): 100 queries a minute per
  client, and the User-Agent `<platform>:<app ID>:<version string> (by /u/<reddit username>)`.
- **Terms:** not read (see above). Training models on Reddit data needs Reddit's approval, which matches our rule:
  Reddit text is never used to train anything.
- **Verdict: Not OK: fallback is the archives,** plus search results that link to threads (titles and links only).
  Whether Reddit's terms allow our use of the archives still needs the same browser check.

### YouTube

- **Used for:** video evidence: titles, descriptions and comments (a later sprint).
- **Sign up:** [Google Cloud console](https://console.cloud.google.com) → enable "YouTube Data API v3" →
  Credentials → API key, restricted to that API. **Env:** `YOUTUBE_API_KEY`.
- **Free limits** ([quota costs](https://developers.google.com/youtube/v3/determine_quota_cost), 3 Nov 2026):
  10,000 units a day. `videos.list` and `commentThreads.list` cost 1 unit; `search.list` has its own allowance of
  100 calls a day; `captions.list` costs 50 and `captions.download` 200. Smoke test: `videos.list` worked, 1 unit.
- **Terms** ([developer policies](https://developers.google.com/youtube/terms/developer-policies),
  [captions.download](https://developers.google.com/youtube/v3/docs/captions/download)): data fetched without a
  user's sign-in may be kept for at most 30 days, then "either delete or refresh the stored data". Pages must "make
  clear to the viewer that YouTube is the source". Captions can be downloaded only with the video owner's sign-in:
  "This method requires the user to have permission to edit the video." The policies also forbid getting API data
  with "any technology other than YouTube API Services", so transcript scrapers are out. A clause on creating "new
  or derived data or metrics" needs a careful read before the video story starts.
- **Verdict: OK with these limits:** titles, descriptions and comments only, **no transcripts**; refresh or delete
  stored YouTube data within 30 days; show YouTube as the source; at most 100 searches a day.
- **If it fails:** video evidence is skipped and `coverage_note` says so. Search results that link to videos
  (titles and links only) still work.

### Jina Reader

- **Used for:** reading public web pages, such as review sites, as clean text.
- **Sign up:** [jina.ai/reader](https://jina.ai/reader); a key is optional. **Env:** `JINA_API_KEY`.
- **Free limits** ([Reader page](https://jina.ai/reader), 3 Nov 2026): 20 requests a minute without a key and 500
  with a free key (the same page's FAQ says 100, so plan for 100); "Every new API key comes with 10M free tokens!"
  Smoke test: read example.com with our key.
- **Terms** ([legal](https://jina.ai/legal), updated 4 May 2026; Elastic's terms now apply after it bought Jina):
  Jina "will only store Input or Output on its servers to the extent required", and it does not use request data
  for training (section 10.4). The same URL is cached for 5 minutes.
- **Verdict: OK with these limits:** public pages only. Never send reddit.com or youtube.com pages through it: that
  would be scraping under their rules.
- **If it fails:** the page is skipped and `coverage_note` says so; search snippets still give titles and links.

### Hugging Face

- **Used for:** downloading public models (embeddings, NLI) for the ML track. Public models need no token.
- **Sign up:** [huggingface.co](https://huggingface.co) → Settings → Access Tokens → a "Read" or fine-grained read
  token. **Env:** `HF_TOKEN` (optional: only for gated models or higher download limits).
- **Free limits** ([rate limits](https://huggingface.co/docs/hub/rate-limits), table dated September 2025): per 5
  minutes, a signed-in free user gets 1,000 API calls, 5,000 file downloads and 200 page requests (500, 3,000 and
  100 without a token). Smoke test: skipped, no `HF_TOKEN`.
- **Terms** ([terms of service](https://huggingface.co/terms-of-service), [tokens](https://huggingface.co/docs/hub/security-tokens)):
  each model's own licence applies ("Open Source license terms are not intended to be replaced or overridden."). For
  tokens: "We also recommend using only fine-grained tokens for production usage."
- **Verdict: OK as planned:** a read-only token, and check each model's licence before using it.
- **If it fails:** models already downloaded keep working; download again later.

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

**Reddit's own API.** It needs Reddit's approval, and access looks closed to new requests (see "Reddit's own API"
above; not yet confirmed from Reddit's own pages). Until access is granted, leave `REDDIT_SOURCE=archive`. If it is:
create a "script" app at reddit.com/prefs/apps, set the three `REDDIT_*` API settings and `REDDIT_SOURCE=official`.
App-only, read-only sign-in: no Reddit password is ever used.

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

## Results table

What each service did for our keys on 3 November 2026. Update a row whenever the smoke test or a terms check
changes it.

| Service | Works? | Limits seen | Context window (LLMs) | Terms OK? | Notes |
|---|---|---|---|---|---|
| Groq | yes | 1,000 requests a day, 8,000 tokens a minute (headers) | 131,072 | yes: does not train or keep inputs | `openai/gpt-oss-120b` reasons before it answers |
| Gemini | yes | not sent; see AI Studio | 1,048,576 | public text only: the free tier trains on inputs | sometimes HTTP 503 when busy |
| OpenRouter | yes | 20 a minute, 50 a day (provider page) | 1,000,000 | public text only: training depends on the host | slowest, about 4 s |
| Cerebras | no: HTTP 402 | none: no free tier any more | 65,536 (unverified) | yes: does not train or keep inputs | needs a plan or credits; team decision |
| Mistral | yes | 188 requests and 625,000 tokens a minute (headers) | 262,144 | public text only: the free plan trains on inputs | `mistral-small-latest` gave 429 with a 0 a minute limit |
| Serper | yes | 2,500 queries in total; 1 used by the smoke test | — | yes, with limits | 9 results for one search |
| Reddit (Arctic Shift) | yes | about 1 request a second; keyword search refused on busy subreddits | — | public archive, no key; read-only, no usernames kept | newest post 3 Nov 2026 09:22 UTC |
| Reddit (PullPush) | no: HTTP 522, then timeouts | — | — | docs unreadable (Cloudflare) | data stops around May 2025 |
| Reddit's own API | not set up | — | — | not confirmed: access looks closed to new requests | see "Reddit's own API" |
| YouTube | yes | 10,000 units a day; 1 used by the smoke test | — | yes, with limits: no transcripts | 30-day keep-or-refresh rule |
| Jina Reader | yes | 500 (or 100) a minute with a key | — | yes, with limits: public pages only | |
| Hugging Face | not checked: no token | — | — | yes: check each model's licence | public models need no token |
| Supabase | yes | Free plan; pauses after 7 days without use | — | see [database.md](database.md) | |

The LLM rows match `backend/config/models.yaml` (US-26): one entry per model with its context window, output limit,
and whether the free tier trains on prompts. Each provider's "trains on inputs" flag, with the terms page it came
from, decides which routes it may appear on (US-46, "LLM routing" above).

## Rotating a key

1. Make the new key at the provider.
2. Put it in your root `.env` in place of the old one (and in the demo laptop's `.env` if it is a team key).
3. Run `python -m scripts.smoke_apis --only <service>` from `backend/`; it must say `ok`.
4. Revoke the old key at the provider.

CI holds no real keys, so nothing changes there.

## If a key leaks

A key "leaks" when it reaches a commit, a pushed branch, an issue, a chat, a screenshot or a log.

1. **Revoke it at the provider first, at once,** before cleaning anything up. Deleting the commit is not enough: a
   pushed key stays in the history, in forks and in caches.
2. Make a new key and put it in your root `.env`; run the smoke test for that service.
3. Tell Palak (CI owner) so the secret scan's result is checked and the history is cleaned if needed, and tell Om.
4. Check the provider's usage page for calls we did not make.
