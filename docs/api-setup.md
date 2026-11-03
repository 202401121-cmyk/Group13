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
| 6 | Reddit | `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` | reddit.com/prefs/apps → create a "script" app | **Read the current Data API terms**: is non-commercial read-only access for a student project allowed? Rate limit |
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

## Results table (fill in, then post in `#dev`)

| Service | Works? | Limits seen | Context window (LLMs) | Terms OK? | Notes |
|---|---|---|---|---|---|
| Groq | | | | | |
| Gemini | | | | | |
| OpenRouter | | | | | |
| Cerebras | | | | | |
| Mistral | yes (3 Nov 2026, `ministral-8b-latest`) | 188 req/min, 625,000 tokens/min (response headers); monthly allowance: see Admin Panel | 262,144 | free plan trains on inputs: public text only | `mistral-small-latest` gave 429 with a 0 req/min limit |
| Serper | | | — | | |
| Reddit | | | — | | |
| YouTube | | | — | | |
| Jina Reader | | | — | | |
| Supabase | | | — | | |

The LLM rows become `backend/config/models.yaml` (US-26): one entry per model with its context window,
output limit, and whether the free tier trains on prompts. Each provider's "trains on inputs" flag, with the
terms page it came from, decides which routes it may appear on (US-46, "LLM routing" above).
