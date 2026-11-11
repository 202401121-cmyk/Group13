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
| [Cerebras](#cerebras) | Nothing while it answers 402: skipped with no call | `CEREBRAS_API_KEY` | **Not OK**: no free tier | failed (HTTP 402) |
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

From `backend/`, with the backend's virtual environment active and your keys in the root `.env`:

```bash
python -m scripts.smoke_apis                       # every configured service except Serper
python -m scripts.smoke_apis --only groq,youtube   # just these services
python -m scripts.smoke_apis --include serper      # also Serper: each run uses one of its 2,500 free queries
python -m scripts.smoke_apis --measure-extraction  # one Groq call the size of a full Reddit source
```

Each service gets the cheapest call that proves its key works:

- **LLMs** (Groq, Gemini, OpenRouter, Cerebras, Mistral): one call through the app's own provider client and
  `backend/config/models.yaml`, with the fixed public prompt "Reply with the single word OK." and 5 output tokens.
  It passes if the reply contains OK. Reasoning models (gpt-oss, nemotron) can spend 5 tokens thinking and send no
  text; only then is the call repeated once with a 200-token cap, and the note says so. Groq gets one call per pool
  model, with a row each, straight at the 200-token cap. Each model's rate-limit headers are compared with
  models.yaml, and any difference is printed in its row. **The headers win:** update the config to match them.
- **Serper:** one search, only with `--include serper`.
- **YouTube:** `videos.list` for one public video (1 quota unit). Never `search.list`.
- **Reddit's own API:** only when `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET` are set: an app-only token, then one
  listing with `limit=1`, with the User-Agent Reddit requires. Never the keyless `.json` or RSS feeds.
- **Jina Reader:** reads `https://example.com` (with `JINA_API_KEY` if set; it also works without one).
- **Hugging Face:** `whoami`, which also shows whether the token is read-only.

A service with no key is "skipped (not configured)", never a failure. Each call gets at most 20 seconds, and one
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

### Last measured: 7 November 2026

`python -m scripts.smoke_apis --only groq`, one call per pool model:

| Model | Status | Latency | Limit headers | After this one call |
|---|---|---|---|---|
| `openai/gpt-oss-120b` | ok | 20,859 ms | 1,000 requests a day, 8,000 tokens a minute | 999 requests, 7,709 tokens left |
| `openai/gpt-oss-20b` | ok | 531 ms | 1,000 requests a day, 8,000 tokens a minute | 999 requests, 7,709 tokens left |
| `qwen/qwen3.8-27b` | ok | 94 ms | 1,000 requests a day, 8,000 tokens a minute | 999 requests, 7,769 tokens left |

The headers match models.yaml. Each model shows 999 requests left after its own single call, so the three limits
are separate: shared limits would have read 999, 998 and 997.

That first call's 20,859 ms was not `openai/gpt-oss-120b`'s usual speed. Three more tiny calls to it, the first on a
new connection, took 990, 1,206 and 1,391 ms: a middle value of 1,206 ms. The slow call was most likely a stalled IPv6 connection:
see [IPv4 only](#ipv4-only-force_ipv4-in-modelsyaml).

`python -m scripts.smoke_apis --measure-extraction`, one call on `openai/gpt-oss-120b`, using the hand-written Reddit
fixtures repeated up to `MAX_SOURCE_CHARS`:

| | Tokens |
|---|---|
| Input | 2,969 |
| Output | 247 |
| **Used** | **3,216** |
| Planned the wrapper's way (its input estimate plus 1,024 of output) | 5,129 |

That was 7 November. The script now sends what extraction sends: its prompt, its message with a laptop's criteria, a thread
capped to 3,000 tokens, its output request, its reasoning settings and its time limit. So a new measurement matches a real
call and won't match this table.

`reddit_tokens_per_search` is **18,000**: 75% of the pool's 24,000 tokens a minute, which leaves each member about
2,000 for shoppers' calls. An intake call plans about 1,700.

That day's pool counted planned tokens, so each member took one full-size source a minute, and the pool about 3.

Extraction (US-14) asks for 1,200 tokens of output on Reddit calls, for the reply and the thinking the reasoning models do
first. A thread near its prompt's limits (10 claims and 15 mentions) can still be cut off, but asking for more would stop a
full-size source fitting a member's minute. It keeps 2,000 of each member's 8,000 free for shoppers, so a member has 6,000
for it. With a laptop's 8 criteria in the prompt:

| Source | Planned | Per member | Pool, per minute | In 18,000 |
|---|---|---|---|---|
| Full size (3,000 tokens) | 5,697 | 1 | **3** | 3.2 |
| Short thread (250 tokens, with its title) | about 2,960 | 2 | 6 | 6 |

A smaller output request can't lift the full-size number. Two calls would need 3,000 each, and the input estimate alone
is about 4,500. While the Reddit budget counted what calls hold, a search read 3 full threads or about 6 short ones, of
the 20 or so research finds. It now counts real use: see below.

#### What Groq counts (9 November 2026)

Pairs of calls with a short answer, read through `x-ratelimit-remaining-tokens`. The minute refills about 133 tokens a
second, so each fall is net of whatever came back before the header was read. The last column is what a prompt plus a whole
request would come to.

| Model | Requested output | Prompt + reply | Remaining afterwards | Fell by | Prompt + request |
|---|---|---|---|---|---|
| `openai/gpt-oss-120b` | 64 | 106 | 7,858 of 8,000 | 142 | 142 |
| | 4,000 | 106 | 7,516 | 342 | 4,078 |
| `openai/gpt-oss-20b` | 1,200 | 95 | 7,666 of 8,000 | 334 | 1,278 |
| | 1,200 | 95 | 7,617 | 49 | 1,278 |

In three of these four, the fall is far below the whole request and nearer real use. The first row can't tell the two
apart: at a request of 64 they differ by only 36. Groq doesn't charge exactly real use either, as a first call can cost more.
Two recordings in `tests/fixtures/recorded` show that, each asking for 1,024: `laptop_coding_70k` fell by 1,638, exactly its
prompt plus request, for a real use of 989, and `research_laptop_coding_70k` fell by 1,074 for a real use of 906. So real
use is the better count, not an exact one. This rests on few calls, and `book_real_use` in models.yaml turns it off if 429s
appear.

The pool now holds a call's input and whole requested output while it runs, then books what it really used. A failed call
books nothing, unless its reply reports what it used: a reply with no text was still charged for. A reply with no token
counts keeps what was held. Bulk work with no room looks again every half second while a running call could still free
enough. A shopper's call doesn't wait for this, as its fallback is quicker.

**This doesn't change how many full-size threads fit a minute: still 3.** A full-size thread really uses about 3,600
tokens, but the next one must hold 5,697, and a member has 6,000 above the reserve. Even with no reserve, 3,600 + 5,697 is
over the 8,000, and even with a perfect estimate, two come to 7,200. It helps when a call uses far less than it holds, such
as a thread with little to say. Only a smaller source cap would change the full-size number.

A live extraction run on 9 November 2026 backs this up at real size. After two calls on each member, the pool counted 2,805,
2,938 and 3,925 tokens. Groq's own count, 8,000 less `x-ratelimit-remaining-tokens`, read 2,707, 3,376 and 3,682, and no
Groq call got a 429.

The 18,000 Reddit budget counts real use the same way: a call's whole request counts while it runs, then what it used.
`book_real_use` turns both back to holds. A thread with no room waits up to 5 seconds while running calls could still free
enough, and that wait comes off its call's time limit. In a fresh run on 9 November 2026, 8 threads really used 10,718 of the
18,000, so the budget no longer sets the limit. The pool's minute does: at about 2,500 held and 1,300 used, each member takes
3 a minute, and 10 more threads could not be read.

In live runs on short threads (9 November 2026), `openai/gpt-oss-120b` and `qwen/qwen3.8-27b` answered in 391 to 783
tokens. `openai/gpt-oss-20b` used all 1,200 on the same kind of thread, so its replies were cut off and that source was
lost.

So extraction now asks these models to think less, per task in models.yaml (`task_settings`): `reasoning_effort` `low` and
`include_reasoning` false on the two gpt-oss models, and `reasoning_effort` `none` on qwen. No other task sends these.
With them, five Reddit threads came back in 16 to 396 output tokens, and none was cut off. `openai/gpt-oss-20b` answered both
of its threads in 25 tokens with no claims, so two threads can't yet show whether low effort makes it miss any.

A search result that holds only a snippet stays a source and a link, but isn't sent to extraction: earlier live runs spent
20 calls a search on them and got no claims. A page that was fetched is read as before.

Public calls to Gemini, Mistral and OpenRouter aren't booked against the pool, so they ask for 2,000. A public page
that Groq reads as the last resort is booked against the pool and asks for 1,200 (`public_output_tokens`, F-6). A
live reply used 566 on 15 mentions alone. Gemini's
thinking is as low as it goes (`reasoning_effort: minimal` in models.yaml; Flash Lite has no `none`), because it counts
against the output limit. On `gemini-3.5-flash`, on a public page with a 1,577-token prompt and a limit of 1,200, thinking
used 1,245 tokens and the reply was cut off at 43. With it off, the
same call stopped normally at 566 tokens, in 5 seconds. The longest reply seen with it off was 1,147 tokens.

### Run the LLM smoke test

With your LLM keys in `.env`, run from `backend/`, with its virtual environment active:

```bash
python -m scripts.smoke_llm
```

It sends one tiny JSON request (fixed public text) to each provider that has a key, Mistral included, through
the same adapter and settings the app uses, and prints a table: provider, model, whether it trains on inputs,
works (yes/no/skipped), latency, tokens used, and the provider's rate-limit response headers. Then it prints our
own limits for each provider that has one: requests and tokens a minute, tokens a day, and OpenRouter's requests a day.
Each call carries extraction's `task_settings` for its model, so a model that refuses them shows up here and not in a
search. It never prints a key, calls real
APIs (so it never runs in CI), and always exits 0.

Then copy what you saw into the results table below and into `backend/config/models.yaml` (model name,
context window, output limit), and remove the `# UNVERIFIED` comment above each row you confirmed.

### Run the Reddit archive smoke test

No key is needed. From `backend/`, with its virtual environment active:

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
- **Free limits** ([rate limits](https://console.groq.com/docs/rate-limits), 7 Nov 2026): `openai/gpt-oss-120b`,
  `openai/gpt-oss-20b` and `qwen/qwen3.8-27b` each have 30 requests a minute, 1,000 a day, 8,000 tokens a minute and
  200,000 tokens a day. The page calls these "the base limits for the Developer plan" next to a Free plan tab, so
  treat them as the most we get. Smoke test: the headers said 1,000 requests a day and 8,000 tokens a minute for all
  three, which matches. `x-ratelimit-limit-requests` counts a day, and `x-ratelimit-limit-tokens` counts a minute.
  `qwen/qwen3.8-27b` is a preview model, so Groq may withdraw it.
- **Terms** ([services agreement](https://console.groq.com/docs/legal/services-agreement), last modified 22 Jun
  2026; [your data](https://console.groq.com/docs/your-data)): "Groq is not permitted to use Inputs or Outputs for
  training or fine-tuning" (section 4.2), and "By default, Groq does not retain customer data for inference
  requests." Reliability and abuse logs may be kept for up to 30 days; Zero Data Retention can be switched on in
  Data Controls. It is for developers building apps; no reselling.
- **Verdict: OK with these limits:** about 1,000 requests a day per model. Switching on Zero Data Retention is a team
  decision.
- **If it fails:** private and Reddit text has no other provider, so the stage uses its own fallback: keyword rules
  for intake, fixed criteria sets for criteria, and skipping the source for extraction. Each model has its own
  limits, so one model failing leaves the other two working ([the model pool](#the-model-pool-pool-in-modelsyaml)).

### Gemini

- **Used for:** LLM calls with public text only. It is first for long public inputs (1M-token window).
- **Sign up:** [aistudio.google.com](https://aistudio.google.com) → Get API key. **Env:** `GEMINI_API_KEY`.
- **Model:** `gemini-3.5-flash-lite`, first for a long public call a shopper waits on, then Mistral, then Groq, then
  OpenRouter. Public bulk work takes turns between Gemini and Mistral; Groq is the last resort after both, then
  OpenRouter (F-6).
- **Free limits** (read 9 Nov 2026): Google's [public page](https://aistudio.google.com/docs/rate-limits) lists no
  numbers for this tier. [AI Studio's page](https://aistudio.google.com/rate-limit), signed in, lists Gemini 3.5 Flash Lite
  at **15 requests a minute, 250,000 input tokens a minute and 500 requests a day**. Other Flash models there have 5, 250,000
  and 20. Daily quotas reset at midnight Pacific time. Gemini sends no rate-limit headers.
- **Thinking:** `reasoning_effort: minimal`. Flash Lite answered HTTP 400 to `none` on all 8 calls, and to `minimal` it
  answered at once.
- **Pacing:** `requests_per_minute` and `tokens_per_minute` in models.yaml. A bulk call with no room waits if that still
  leaves a full attempt's time (up to 5 of the stage's 15 seconds), and otherwise goes to the next provider on its route
  with no call to Gemini. The 500 a day isn't paced: a 429 from it cools Gemini down, and calls move on.
- **Live check** (9 Nov 2026): 10 review pages started together, from the caches. Flash Lite answered all 10 with no 429, in
  1.2 to 3.2 seconds each, and 4 of the pages gave 11 claims. On `gemini-3.5-flash` at 5 a minute the same pages gave 5
  answers, and the other 5 moved on: Mistral timed out twice at the 9-second attempt limit and answered one 429, and 2
  OpenRouter calls had not answered by the stage's limit. So those two are weak backups for a burst.
- **Terms** ([Gemini API terms](https://aistudio.google.com/docs/terms), effective 23 Mar 2026): on the unpaid tier,
  what we send is used to improve Google's products, and "human reviewers may read, annotate, and process your API
  input and output." Also: "Do not submit sensitive, confidential, or personal information to the Unpaid Services."
- **Verdict: OK as planned:** public text only (`trains_on_inputs: true`).
- **If it fails:** the next provider in the public route. The free tier sometimes answers HTTP 503 when busy.

### OpenRouter

- **Used for:** LLM calls with public text only, always last: the fallback after Gemini, Mistral and Groq, for a shopper's
  call and for public bulk work alike (`rotates: false`). It takes no turn in the rotation, because its free models
  allow 50 requests a day and answer slowly. Paced at 20 requests a minute, and its 50 requests a day are counted
  (`requests_per_day`), so a call past them is skipped instead of earning a 429.
- **Sign up:** [openrouter.ai](https://openrouter.ai) → Keys. **Env:** `OPENROUTER_API_KEY`.
- **Free limits** ([limits](https://openrouter.ai/docs/api-reference/limits), 3 Nov 2026): 20 requests a minute on
  `:free` models; 50 a day until the account has bought at least 10 credits, then 1,000 a day. Smoke test: ok, in
  about 4 s.
- **Terms** ([privacy and logging](https://openrouter.ai/docs/features/privacy-and-logging)): whether prompts are
  used for training depends on the model's host. OpenRouter says prompts "will not be trained on, but there are
  exceptions." Hosts that train can be blocked for the account or per request; we have not confirmed which host
  serves our model.
- **Verdict: OK as planned:** public text only, about 50 requests a day.
- **If it fails:** it is last, so the stage uses its own fallback.

### Cerebras

- **Used for:** nothing while it answers 402. It is skipped with no call.
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
- **While it answers 402:** `available: false` in models.yaml, so it is skipped with no call. It left the private and
  Reddit routes on 7 Nov 2026 and the public long route on 9 Nov 2026. Its "trains on inputs" flag stays. If the
  account gets credits, set `available: true`, run the smoke test, and put it back in those routes as a team decision.

### Mistral

- **Used for:** LLM calls with public text only. It is first for short and medium public jobs.
- **Sign up:** [console.mistral.ai](https://console.mistral.ai) → API Keys. **Env:** `MISTRAL_API_KEY`, plus
  `MISTRAL_MODEL`, `MISTRAL_RPM`, `MISTRAL_RPS`, `MISTRAL_TPM` and `MISTRAL_DAILY_TOKENS` (see "Mistral" under LLM
  routing).
- **Free limits** ([plan tiers](https://docs.mistral.ai/admin/user-management-finops/tier), 3 Nov 2026): shown only in
  the Admin Panel → API → Limits. **The rate-limit headers of a live reply on 10 Nov 2026 said 2 requests a minute and
  40,000 tokens a minute** for `ministral-8b-latest` (`x-ratelimit-limit-req-minute: 2`,
  `x-ratelimit-limit-tokens-minute: 40000`, read with `python -m scripts.smoke_apis --only mistral`). On 3 Nov 2026 the
  same headers had said 188 requests and 625,000 tokens a minute, so the limits changed; read them again if Mistral
  answers 429s.
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
- **Our limits** (`backend/config/research.yaml`): at most 6 queries a search, 60 a day and 2,400 in total, and 5 a
  second, which is our own choice because Serper's docs give none we could confirm. See [Research](#research-us-12-us-27).
- **If it fails or runs out:** research carries on with the Reddit archives, and `coverage_note` says web search and
  review sites were unavailable. A query already in the 24-hour cache still answers.

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
| private | groq | groq | groq |
| reddit | groq | groq | groq |
| public | mistral, groq | mistral, gemini, groq, openrouter | gemini, mistral, groq, openrouter |

Groq's free tier has a small daily token allowance, so public work goes to Mistral and Gemini first and Groq is
kept for private and Reddit text. Public bulk work reaches Groq only as the last resort (F-6, 10 Nov 2026): when
neither Gemini nor Mistral has room, and only if a Groq model has room right now, outside the shopper's reserve. Such
a page asks for a 1,200-token answer (`public_output_tokens` in `pool:`) so that a page at the 3,000-token cap goes
whole, and a page that still can't go whole is skipped, never trimmed. Long public inputs start on Gemini for its 1M-token window. Groq in a route means all of its
models, in models.yaml order. Cerebras is skipped while it is unavailable, and no private or Reddit route may name an
unavailable provider.

### The model pool (`pool:` in models.yaml)

Each Groq text model is a member of the pool with its own limits. Read from
[Groq's rate-limits page](https://console.groq.com/docs/rate-limits) on 7 Nov 2026:

| Model | Requests a minute | Tokens a minute | Tokens a day | Requests a day | Window |
|---|---|---|---|---|---|
| `openai/gpt-oss-120b` | 30 | 8,000 | 200,000 | 1,000 | 131,072 |
| `openai/gpt-oss-20b` | 30 | 8,000 | 200,000 | 1,000 | 131,072 |
| `qwen/qwen3.8-27b` (preview) | 30 | 8,000 | 200,000 | 1,000 | 131,072 |

- **Rotation:** bulk work (`extract_claims`, `enrich_extract`) goes round every model its route allows, in turn (F-3,
  10 Nov 2026): Groq's three models for private and Reddit text (D-51), and for public text Gemini and Mistral.
  For public text Groq's models take no turn: they follow Gemini and Mistral as the last resort, in their fixed order,
  never waited for, and they move no rotation. OpenRouter takes no turn (`rotates: false` in models.yaml): it follows
  them all as the fallback, used only when none has room, and its 50 requests a day are counted. Each is paced by its own limits: these members by the table above,
  Gemini at 15 requests a minute, Mistral at 2 a minute and 40,000 tokens a minute (its headers, 10 Nov 2026),
  OpenRouter at 20 a minute. A model whose window can't hold the prompt and its answer is
  skipped, and the next one takes the turn; a longer input still fits by being split into chunks, as before (public
  pages are capped at 3,000 tokens, so this is rare). Calls a shopper waits on keep the route's order, with the rest
  as backups.
- **Pacing:** each member counts its requests and tokens over the last minute: the input the wrapper measures plus
  the requested output while a call runs, then what it really used, or nothing if it failed. A member with no room is passed over for one with room. If all are busy, the call waits for
  the soonest slot, so it doesn't earn a 429. A public page on Groq, the last resort, never waits: with no room right
  now, the next provider is tried.
- **Time limit:** `complete()` has no time-limit argument, so the limit comes from the task's stage and is counted from
  each `complete()`. A stage that calls it twice gets the limit twice, but `run_stage` still stops waiting on time and
  uses the fallback. One attempt gets a timeout of at most 60% of the limit, so another member has time to answer if it
  hangs. httpx applies a timeout to each phase of a request, and a provider that keeps sending keep-alive bytes resets
  its read timer: in P18's drill an OpenRouter attempt allowed 9 s ran 27.6 s. So the adapter reads the reply as it
  arrives and cuts the attempt off at the first chunk past its timeout (F-6). The bound is that timeout plus one gap
  between chunks, since httpx's read timeout still ends a silence; a connection that sends nothing at all is ended by
  httpx's own timeout. A 429 cools that member and the call moves on at once. No call starts in the last second. If no member can take it by then, it raises `LLMUnavailable` and the stage uses
  its fallback.
- **One call's size:** input is fitted to the member's tokens a minute (less the reserve, for bulk work) as well as its
  window, because a bigger call could never get a slot. Text too long for that is trimmed to one call, not chunked, because chunks over a minute's
  tokens couldn't all go out before the stage's limit. Callers put the most important text first, so the
  lowest-ranked comments go. A public page is never trimmed for Groq: that member is skipped instead.
- **Daily budget:** a call's planned tokens are held before it runs, then swapped for what it really used, so
  calls running together can't pass the budget. This covers provider budgets too. The count is in memory, per UTC day.
- **Reddit budget:** the extract stage keeps a search's Reddit tokens within `reddit_tokens_per_search`: a call's whole
  request while it runs, then its real use. A thread past it is skipped with no call, and the coverage note says how many. See
  [Last measured](#last-measured-7-november-2026).
- **Room for shoppers:** bulk work never takes a member below `extract_reserve_tokens_per_minute` (2,000) of its current
  minute, so intake and reply parsing, which plan about 1,700, always find room. Extraction waits inside its 15-second
  limit, or skips the source with a coverage note. A call a shopper waits on may use the reserve.
- **Sources per search:** `max_sources_per_search` in `config/extract.yaml` (40) limits how many sources one search sends
  to a model. `EXTRACT_MAX_SOURCES` overrides it, for a live run with a small budget.

A member's limits sit in `pool:`, not on its `models:` row, because that row is the `ModelLimits` contract.

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
| `app/extract/stage.py` | `extract_claims` | one fetched source's retrieved text, never a search snippet alone. Criterion keys and the category label from our config in every call; any others, which may hold the shopper's words, in Reddit calls only | reddit for Reddit, otherwise public | long |

`detect_intent`, `classify_reply`, `answer_user_question`, `write_question` and `write_explanation` always carry
the shopper's words, so the wrapper refuses any class but private for them (`PERSONAL_TASKS`). A new call site
must pass `data_class=` and `difficulty=`, and be added to this table and to
`backend/tests/unit/llm/test_call_sites.py`.

### Mistral

- Model: `MISTRAL_MODEL`, default `ministral-8b-latest` (Ministral 3 8B, 256k-token window). On 3 Nov 2026 it
  answered our JSON check; `mistral-small-latest` answered HTTP 429 with a limit of 0 requests a minute on our key.
- JSON mode is on (`response_format: {"type": "json_object"}`). Replies are still checked against the schema,
  and invalid JSON is retried once, then the next provider is tried.
- Limits: 2 requests a minute and 40,000 tokens a minute (`requests_per_minute`, `tokens_per_minute`), shared by the
  whole app. They are Mistral's own, read from the rate-limit headers of a live reply on 10 Nov 2026; on 3 Nov 2026
  the headers had said 188 requests and 625,000 tokens a minute. `MISTRAL_RPM`, `MISTRAL_RPS` and `MISTRAL_TPM` in
  `.env` override them, so leave them empty unless Mistral's headers change.
- Daily budget: `MISTRAL_DAILY_TOKENS=1000000`. Mistral shows its monthly allowance only in its Admin Panel →
  API → Limits, so this is a cautious guess: set it to that allowance ÷ 31, less 10%. The count is kept in
  memory, per UTC day, and starts again at zero when the app restarts.

### Daily token budgets

Every provider has a daily token counter (F-4, 10 Nov 2026), `daily_tokens` in models.yaml. A call that would pass it
is not sent (`skipped_budget`), and the next model is tried. Each can be changed in `.env` with
`<PROVIDER>_DAILY_TOKENS`. The count is in memory, per UTC day. Where a provider publishes no token limit, the number
is the team's choice, and the row says how it was set. For Gemini and OpenRouter that is their requests a day times
5,000, the tokens the wrapper plans for one full source.

| Provider | Tokens a day | Where it comes from | Override |
|---|---|---|---|
| Groq | 600,000 | 3 models × 200,000, [rate limits](https://console.groq.com/docs/rate-limits), 7 Nov 2026. Each model also keeps its own 200,000 in `pool:` | `GROQ_DAILY_TOKENS` |
| Gemini | 2,500,000 | The team's choice: no token limit is published; 500 requests a day ([AI Studio](https://aistudio.google.com/rate-limit), 9 Nov 2026) × 5,000 | `GEMINI_DAILY_TOKENS` |
| OpenRouter | 250,000 | The team's choice: no token limit is published; 50 requests a day ([limits](https://openrouter.ai/docs/api-reference/limits), 3 Nov 2026) × 5,000 | `OPENROUTER_DAILY_TOKENS` |
| Cerebras | 1,000,000 | 1M tokens a day while a trial credit lasts, [rate limits](https://inference-docs.cerebras.ai/support/rate-limits), 3 Nov 2026. Unavailable today | `CEREBRAS_DAILY_TOKENS` |
| Mistral | 1,000,000 | The team's choice: a cautious guess, 3 Nov 2026, until someone reads the monthly allowance in Mistral's Admin Panel and sets it to that ÷ 31, less 10% (see "Mistral" above) | `MISTRAL_DAILY_TOKENS` |
| DeepSeek | 500,000 | The team's choice (D-63, 11 Nov 2026): no free tier is described ([pricing](https://api-docs.deepseek.com/quick_start/pricing)), so it runs only on a granted balance. Unavailable until S3-1 gives it routes | `DEEPSEEK_DAILY_TOKENS` |

OpenRouter also has a requests-a-day counter (`requests_per_day: 50`): its free models allow 50 requests a day until
the account has bought 10 credits ([limits](https://openrouter.ai/docs/api-reference/limits), 3 Nov 2026, checked
again 10 Nov 2026: 20 a minute and 50 a day below 10 credits). Every
request that goes out counts one, whatever comes back: a page split into chunks counts one per chunk sent. A call held
back first (cooling, or no room in its minute) counts none, and a call past the 50 is not sent (`skipped_budget`).

### When a provider fails

A pool member has its own record; any other provider has one for the whole provider.

- HTTP 429 or 5xx: skipped for as long as it asks (Retry-After or a reset header), held between 60 and 120 seconds.
  With no usable header, 90 seconds.
- Three timeouts or other failures within 60 seconds: skipped for 90 seconds, then one trial call. A timeout we cut
  short because the stage was nearly out of time doesn't count: that says nothing about the model.
- HTTP 401 or 402 (bad key, or the account needs a plan): the whole provider is skipped for the rest of the run,
  because its models share the key.
- HTTP 403 (this key may not use this model): that model is skipped for the rest of the run.
- The error code `model_decommissioned` (a retired model, which Groq answers with HTTP 400): that model is skipped for
  the rest of the run. Groq's errors page lists no codes, so this one comes from public reports of its replies.
- HTTP 404, or the error code `model_not_found` or `invalid_model`: that model is skipped for 10 minutes, then gets one
  trial call. If it answers, it is back. If it is still not found, it waits another 10 minutes; any other failure waits
  as this list says. A later failure never shortens a cool-down. So a gateway blip or brief outage recovers by itself.
  A wrong `base_url` looks the same, so if every model of a provider goes this way, check the URL.
- Over its daily token budget, or its requests a day: not sent, logged with outcome `skipped_budget`.
- No room before the stage's time limit: not sent, logged with outcome `rate_limited`.
- Every attempt writes one call record: task, data class, provider, model, tokens, time and outcome. Never the
  prompt or the reply.

### IPv4 only (`force_ipv4:` in models.yaml)

On some networks, an IPv6 connection to a provider hangs until it times out, and only then moves on to IPv4: 21 seconds
when we measured it with nothing cutting it short. It happens only when the system lists IPv6 first, so it comes and
goes. The app stops waiting sooner, but the wait still uses up most of a stage's time limit, so the stage can quietly use
its fallback. So `force_ipv4: true`, the default, connects to every provider over IPv4 only. The smoke commands build
their client the same way, so their LLM checks see what the app sees.

If a proxy would carry the provider's request (`HTTPS_PROXY` or `ALL_PROXY` in either case, or the system proxy for HTTPS),
the app uses it as usual, skips IPv4 only and logs that once. The proxy makes the outbound connection, so the stall isn't
ours there. `HTTP_PROXY` alone doesn't count: provider calls use HTTPS, so it would carry none of them and IPv4 only stays
on. A host listed in `NO_PROXY` still connects directly and can stall.

If a client can't be built at all, for example with `ALL_PROXY=socks5://...` and no SOCKS support, every provider is
unavailable for that run. It is logged once, and stages use their fallbacks. We don't install SOCKS support.

To turn the setting off, set `force_ipv4: false` in `backend/config/models.yaml`. You need that on a network with no IPv4.

## Research (US-12, US-27)

The research stage (`backend/app/research/stage.py`) builds its queries in code from the rubric, then reads three kinds of
source at once, with no LLM call. One failing never stops the others. A source type that failed or timed out is named in
`coverage_note`.

| Source | How | Kept as |
|---|---|---|
| Reddit threads | The archives below, with comments for the top 8 threads by score (`reddit_comment_threads`) | `reddit`, with `community`, `upvotes`, `comment_count`, `comments` |
| Web search | Serper, 10 results a query | `web_search`, with `site` and the snippet as text |
| Review sites | Pages on the sites below that Serper finds, read directly | `review_site`, with `site` |

A search result pointing at Reddit keeps its title only, for counting candidates, and is never a source (D-53). One
pointing at YouTube keeps its title and link only.

**Queries.** 4 to 6, the same for the same rubric. There are 4 with no budget or use, one more for each. The first round
sends at most 5 to Serper and holds one slot: "X vs Y" if two candidates are known, otherwise the sixth query. The slot waits
for Reddit's listing, whose titles name products more often, but for half the deadline at most, and it isn't spent if under a
tenth of the deadline is left. Once the first round is in, or at that halfway point, review pages are queued best-ranked first,
so the caps pick the same pages from the same answers. A candidate
is a product-like name in the titles of at least two different sources, found by a rule with no LLM. It misses names with
no digit, such as `MacBook Air`, and names whose only number has 4 or more digits.

**The shopper's words stay here.** The use case is what the shopper typed, so a query never carries it as typed. A query
holds a use only if the use case names one from `known_uses` in `config/research.yaml` (intake keeps no list of its own), and
then only as written there: "mostly coding at home" searches for `coding`. Anything else counts as no use, so a name, place,
email or phone number in the use case can't reach Serper or PullPush. The category is not covered: one intake doesn't
recognise goes out as the shopper named it, because a search for an unlisted product needs it.

**Parallel fetch.** One thread pool of a fixed size (`pool_size`, default 6). The stage returns at `deadline_s` (36 s, inside
its 40-second limit) with whatever finished. Near-duplicate threads, with more than 60% of their title words shared, are
merged: the higher-scored thread stays, with the others' URLs as the last lines of its text, because a `Source` has one
`url`. Every word counts, however short and in any script: the 3 in `Swift 3` is what makes it a different laptop.

| Provider | Rate limit | Per search | Per day | In total |
|---|---|---|---|---|
| Serper | 5 a second | 6 | 60 | 2,400 |
| Reddit archives | 1 a second (Arctic Shift), 1 every 2 s (PullPush) | 14 | 500 | |
| Review sites, robots.txt included | 1 every 2 s per site, or its longer `Crawl-delay` (over 10 s skips the site) | 24 | 200 | |

**Counts.** Each request really sent is counted in the git-ignored `backend/.cache/research/usage.json`, per UTC day and in
total, so the counts survive a restart. A cache hit is free. A capped provider is skipped, and `coverage_note` says it "ran
out of quota". A capped Reddit still answers from its cache, because that costs no request. The total is for each machine:
a key shared between machines uses its 2,500 faster than one count shows. If the file can be read but not saved, this
process keeps its own larger count, so a cap still holds.

**Coverage note.** Every different failure of a source type is named, so a timeout can't hide a cap. A subreddit that fails
while the others answer is named too, and so is a comment read that fails. So is Arctic Shift failing when PullPush then finds
nothing. These are not named: a subreddit or thread the archive doesn't have; PullPush failing after Arctic Shift found nothing;
and one page failing while other pages were read, unless the failure is a cap.

**Cache.** Search results, pages and robots files for 24 hours, in the git-ignored `backend/.cache/research/`. A failure is
never cached. `RESEARCH_CACHE=off` turns it off and `RESEARCH_PAGES=off` stops page fetching, both for recording.

**Review sites** (`review_domains` in research.yaml): 91mobiles, Smartprix, Digit, Gadgets 360, Notebookcheck, GSMArena and
RTINGS. Each page is read with our User-Agent, and only if its `robots.txt` allows it. That file is read by our own RFC 9309
parser (`app/research/robots.py`), because Python's misses wildcards and takes the first matching rule, not the longest. A
missing file allows. An unreachable one (5xx, 429, no reply, or a redirect off these sites) says no until the next search
asks again, and 401 or 403 says no. HTML only, at most 1.5 MB, 10 s for each read, a body cut off once it has taken 15 s
(checked as data arrives, so a stall can add one more read's 10 s), and redirects only within these sites. A page with under 500 characters of text probably needs JavaScript, and is skipped until US-38; if
Serper found it, its snippet stays as a `web_search` source. One page linked two ways (a trailing slash, or a redirect) is
one source.

**What their `robots.txt` said** (8 Nov 2026, the first live run): 91mobiles, Smartprix, Digit and RTINGS allow us, and
Gadgets 360 disallows `/` for us, so it is skipped. Notebookcheck and GSMArena did not come up in that run, so theirs was
not read. Each is read again when it is needed, at most once in 24 hours.

**Open (Jenish):** these sites' terms have not been read. `robots.txt` says what a crawler may fetch, not what we may do with
the text, so a person needs to read each site's terms and record the verdict here. Removing a site is one line of config.

**Untrusted text (US-28).** Everything we read from outside is data, never instructions. These layers don't rely on the model
behaving, and `tests/faults/test_hostile_pages.py` proves them with a fake model that obeys any order it can see:

- **One cleaning function** (`clean_text` in `app/research/cleaning.py`), used by every adapter. It strips tags, scripts,
  styles, comments and what's hidden from readers, removes zero-width and control characters, and normalises to NFC.
- **Visible orders are removed** there too: overriding instructions, fake system messages, addressing AI readers, "rank X
  first", "remove X from the results" and JSON answers. **This catches plain wordings only**, and a test pins a rewording
  that gets through, so the other layers still matter. Text hidden by a stylesheet isn't seen as hidden either, because the
  parser has no CSS.
- **A cap per source** (`max_source_tokens`, 3,000, counted the way the wrapper counts): whole paragraphs, and for Reddit the
  highest-scored comments first.
- **Labelled data:** retrieved text reaches a model only through `wrap_as_data()`, in a block whose marker is a hash of its
  text, so a page can't close it. `assert_only_in_data_block` in `tests/fakes/prompt_checks.py` checks any prompt.
- **Schema JSON only:** for calls that read retrieved text the whole reply must be the JSON, or one fence holding only it. Extra
  fields and prose are refused, retried once, and the source is then dropped. Code then checks each claim's quote, product
  and criterion against the source.

A green harness proves those ten attacks fail, not that every attack does. Today's decide stage is a stub, so the ranking
comparison can't fail yet; the harness also compares the cleaned text, the claims and a small ranker over the claims.

## Reddit (US-12)

Reddit threads are community evidence: what owners say after weeks of use. We read them through public archives,
not by scraping reddit.com.

| Source | When it is used | Freshness | Our limits |
|---|---|---|---|
| Arctic Shift (`ARCTIC_SHIFT_BASE_URL`) | Always first (`REDDIT_SOURCE=archive`, the default) | A few days behind; comments arrive a day or two after their thread | 1 request a second for the whole app; on 429 or 422, wait for `X-RateLimit-Reset` (at most 30 s) and retry once; paused 10 minutes after 3 failures |
| PullPush (`PULLPUSH_BASE_URL`) | Only if Arctic Shift failed or found nothing | Stopped around May 2025: results say "Reddit via older archive (to May 2025)" | 1 request every 2 s; one retry; paused 30 s after 3 failures |
| Reddit's own API (PRAW, read-only) | Only with `REDDIT_SOURCE=official` and all three `REDDIT_*` API settings | Live | PRAW's own rate limiting, plus our budget and breaker |

Every request has a 10 s timeout and the User-Agent `ShopSense/<version> (IT314 student project;
+https://github.com/202401121-cmyk/Group13)`. One search uses at most 14 requests (`RequestBudget`): one per
subreddit (up to 4), 8 for comment threads and 2 for retries. Threads are scored from the listing first, as the score needs no
comment text, and comments are read one at a time, best score first, until the 8 are done or research's deadline comes.
Threads past the 8 stay as sources with their listing data, and what was read before a deadline is kept. Cleaned results are cached (see "Keeping Reddit data") in memory and in the
git-ignored `backend/.cache/reddit/`; a failure is never cached. Breakers and rate limiters last as long as the process, so
one search's failures count for the next. A thread's title, text and comments have HTML entities and invisible characters
removed before they are stored.

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
- A thread's comments keep their depth and parent, and each author only as `author_hash`.

**How threads are found.** Arctic Shift's keyword search (`title`, `selftext`, `query`) is "not supported with
very active subreddits": on 3 Nov 2026 it answered 422 at once for r/SuggestALaptop and r/laptops. So we ask for
each subreddit's newest 100 posts in a date window and match the query's words ourselves; one cached listing
serves every query. The dates aren't in its cache key, so an entry lasts its age limit and doesn't end at midnight UTC. The window ends 2 days before today, because the archive has a thread's comments only a day
or two after it is posted (threads from 31 Oct had theirs on 3 Nov; most from 1-2 Nov had none).

**Where Reddit's time went** (9 November 2026, empty cache, 36-second deadline). 12 requests in all. Comment reads took 2.0 to
2.6 seconds each, one at a time, and 5 of the 8 finished before the deadline. So replies aren't the slow part: the listings
are. Arctic Shift answered the first listing with HTTP 422 in 0.9 seconds, and the retry went out 15 seconds later, the wait
it asks for. A later listing got 422 twice and was lost, so only 2 of the 3 subreddits were listed.

So a 422 now waits at most 5 seconds and retries once, then the next subreddit is tried. A 429, and PullPush, keep their 30
seconds. No wait runs past research's deadline, and each leaves a second for the reply. A 422 means the archive is up, so it
never counts toward the 10-minute pause that real failures lead to. In a live run the same day, with an empty cache, one 422
cost 5 seconds, 7 of the 8 comment reads finished before the deadline, and they took 1.2 to 7.5 seconds each.

**Who counts as a recommended product.** Extraction marks what is a product at all (`is_product`). The recommender counts then
skip any name made only of the `brands` and `generic_words` in `config/reddit_quality.yaml`: "dell", "gaming laptop" and "dell
laptop" don't count, while "MacBook", "WH-1000XM5" and "Apple Watch" do, as each has a word of its own. Parts and odd names
such as "5060 rtx" still count for now.

**What each LLM call leaves.** Every attempt records its provider, model, task, start, duration, status and token counts, and
never any text. `python -m app.cli "..." --claims` ends with a summary per provider: calls, median and slowest time, timeouts
and 429s. Only calls that went out are counted. A call our own limits refused has no input tokens, so it isn't counted as a
call, or as a 429. A call still running when the search ends isn't in the summary, because its record isn't written yet.

| Provider, live run on 9 November 2026 | Calls | Median | Slowest | Timeouts | 429s |
|---|---|---|---|---|---|
| Gemini 3.5 Flash Lite | 10 | 7.9 s | 9.0 s | 1 | 0 |
| Groq | 7 | 1.2 s | 2.0 s | 0 | 0 |

Each attempt may take 9 of a source's 15 seconds. Flash Lite answered in 1.2 to 3.2 seconds earlier that day, on the same
pages, so its speed changes from one run to the next.

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

**Keeping Reddit data.** D-53 allows it at most 24 hours, counted from when it was read, and covers every copy we hold. Storing
a thread again with its comments doesn't restart the clock.

- **The cache** serves an answer only while it is younger than 24 hours minus `SEARCH_RUN_KEEP_MINUTES`: 23.5 hours by default.
  Search results and review pages keep their full 24 hours. The only Reddit words among them are the titles of results that
  point at Reddit. Those are used to spot product names and never reach a finished run.
- **A finished run** stays in the server's memory for `SEARCH_RUN_KEEP_MINUTES`, limited to 1 to 120 minutes, and that copy does
  hold Reddit text. So a run that read the cache at the last moment goes at about the 24 hours: later by the length of the
  search itself, up to 150 seconds. A run past its time is dropped the next time any search starts, ends or is looked up, so
  on an idle server it can stay in memory until then, though nobody can fetch it.
- **Files** past the limit are deleted by the next search, even with `REDDIT_SOURCE=off`, `RESEARCH_CACHE=off` or no working
  client. A machine that runs no search deletes nothing until it does.
- **A saved search** holds no Reddit text at all: a Reddit source keeps its URL, community, fetch time, archive and counts
  (`app/research/retention.py`, and see [database.md](database.md)). US-14 and US-16 will fill fields that can carry Reddit's
  words, and must extend that module before those reach a saved row.

**Open question (for Om).** We keep a quote's source for evidence. If a thread or comment is deleted on Reddit
after we read it, should evidence we have kept for longer than the 24-hour cache be dropped too? Today no Reddit text
leaves the caches past 24 hours and none is saved, so the question matters once US-30 saves evidence to the database.

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
| Groq | yes, 3 models (7 Nov) | per model: 1,000 requests a day, 8,000 tokens a minute (headers) | 131,072 | yes: does not train or keep inputs | `openai/gpt-oss-120b` reasons before it answers |
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
3. Run `python -m scripts.smoke_apis --only groq` from `backend/`, with your service's name in place of `groq`; it
   must say `ok`.
4. Revoke the old key at the provider.

CI holds no real keys, so nothing changes there.

## If a key leaks

A key "leaks" when it reaches a commit, a pushed branch, an issue, a chat, a screenshot or a log.

1. **Revoke it at the provider first, at once,** before cleaning anything up. Deleting the commit is not enough: a
   pushed key stays in the history, in forks and in caches.
2. Make a new key and put it in your root `.env`; run the smoke test for that service.
3. Tell Palak (CI owner) so the secret scan's result is checked and the history is cleaned if needed, and tell Om.
4. Check the provider's usage page for calls we did not make.
