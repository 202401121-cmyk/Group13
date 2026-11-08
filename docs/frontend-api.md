# Backend API for the frontend

The one page to read before calling the backend from `frontend/`. It lists every route the backend serves, documents
the search route in full, and says what the backend cannot do yet, so the screens never invent it.

The pre-search routes (intake, criteria, interview) have their own detailed page: [api/presearch.md](api/presearch.md).

## The basics

- **Base URL:** `VITE_API_BASE_URL` in `frontend/.env.local` (default `http://localhost:8000`). Every route is under `/api`.
  Start the backend from `backend/`, with its virtual environment active: `uvicorn app.main:app --port 8000`.
- **CORS:** the backend accepts browser calls only from the origins in `FRONTEND_ORIGINS` (comma-separated, in the
  root `.env`; default `http://localhost:3000`, the frontend's dev server). `*` is never allowed. If the browser
  console shows a CORS error, add the page's origin there and restart the backend.
- **JSON in, JSON out.** Send `Content-Type: application/json`. The backend allows only `GET`, `POST` and `PUT`, and
  only the `Content-Type` header.
- **Errors** are always `{"detail": "<one plain sentence>"}`, never a stack trace. Show `detail` as it is.
- **Types:** every request and response is a contract model. The JSON Schemas are in `shared/contracts/schema/`,
  and the TypeScript types are generated from them into `frontend/src/types/contracts/` (`npm ci` does it). Never
  write these types by hand.

## Every route the backend serves

These nine routes are the whole API, and they match the backend's OpenAPI (`GET /openapi.json`). The frontend calls
all of them except `GET /api/health`, which `scripts/dev.py` uses to see that the backend is up.

| Route | What it does | Request body | Response 200 | Errors | Details |
|---|---|---|---|---|---|
| `GET /api/health` | Is the backend up; is the database `ok`, `off` or `down` | none | `{"status", "schema_version", "database"}` | none | [presearch.md](api/presearch.md#get-apihealth) |
| `POST /api/presearch/intake` | Reads the shopper's request; starts a session | `text` (up to 1,000 characters; blank text gets a follow-up question); `session_id` (optional) | `session_id`, `intent`, `ready_for_research` | 404, 422 | [presearch.md](api/presearch.md#post-apipresearchintake) |
| `PUT /api/presearch/{session_id}/intent` | Changes what we understood (the use, budget or region) | `changes` (the `Intent` fields to change; a new product, must-have or leave-out is refused with 422) | the session's new `Intent` | 404, 409, 422 | [presearch.md](api/presearch.md#put-apipresearchsession_idintent) |
| `POST /api/presearch/{session_id}/criteria` | The criteria to compare | none | `CriteriaSet` | 404, 409 | [presearch.md](api/presearch.md#post-apipresearchsession_idcriteria) |
| `POST /api/presearch/{session_id}/interview/start` | The first question | none | `InterviewStep` | 404, 409 | [presearch.md](api/presearch.md#post-apipresearchsession_idinterviewstart) |
| `POST /api/presearch/{session_id}/interview/reply` | Answers the current question | `reply_text` (1 to 500 characters); `question_id` (optional: makes a repeated reply safe); `level` or `exact_weight` (optional, never both: [rough answers](#rough-answers-us-08)) | `InterviewStep` (next question, rephrase, or finished with the rubric) | 404, 409, 422 | [presearch.md](api/presearch.md#post-apipresearchsession_idinterviewreply) |
| `POST /api/presearch/{session_id}/interview/back` | Asks an earlier question again | `question_id` (1 to 64 characters) | `InterviewStep` | 404, 409, 422 | [presearch.md](api/presearch.md#post-apipresearchsession_idinterviewback) |
| `GET /api/presearch/{session_id}` | Everything about the session, for a page reload | none | the session | 404 | [presearch.md](api/presearch.md#get-apipresearchsession_id) |
| `POST /api/search` | Runs the search with the finished rubric | `SearchRequest` | `SearchResult` | 422 | below |

What each error means:

| Status | When | `detail` |
|---|---|---|
| 404 | The session is unknown or has expired | `This session has expired. Please start a new search.` |
| 409 | A step was called out of order | Says which step comes first |
| 422 | The body is not valid | Names the field, and never repeats what was typed |

Trust this page for errors, not the OpenAPI. The OpenAPI lists no 404 or 409, and it shows 422 as FastAPI's list of
errors, when the real answer is the one plain sentence above.

FastAPI also serves its own pages, `GET /docs` and `GET /redoc`, which show the OpenAPI. They describe the API and are
not part of it. A path that doesn't exist answers 404 with `Not Found`, and a route called with the wrong method
answers 405 with `Method Not Allowed`, both in `detail`.

## POST /api/search

Runs every search stage (research → extract → match → verify → enrich → decide → explain → commerce) for one
finished interview and returns the ranked results. It answers once, at the end: there is no progress stream yet.

**Request body:** a `SearchRequest` (`shared/contracts/schema/SearchRequest.json`).

| Field | Type | Notes |
|---|---|---|
| `session_id` | string | The `session_id` from intake (`sess_` and 24 hex characters). Never make one up. |
| `user_id` | string or `null` | Always `null` until login (Sprint 4). A guest's search is stored only under the anonymous `session_id`, and is never linked to an account. |
| `raw_query` | string, 1 to 1,000 characters | The request as the shopper typed it. |
| `rubric` | `Rubric` | The `rubric` from the interview's `finished` step, unchanged. |

**Response 200:** a `SearchResult` (`shared/contracts/schema/SearchResult.json`; a full example is in
`shared/contracts/examples/search_result.json`).

| Field | What the screen does with it |
|---|---|
| `search_id`, `request`, `started_at`, `finished_at`, `schema_version` | Identify the search; `request` echoes what was sent, and `schema_version` is the version of the contracts. |
| `products` | One `ProductSummary` per product that was looked at: the product, its resolved values and community signal. Look up names here by product ID. |
| `decision` | The ranking: `ranked` (in order, with `score`, `coverage` and `tied_with`) and `filtered` (dropped by a hard rule, with the reason). Every ranked product is in `products`. |
| `explanations` | Why each product is where it is, up to 8 sentences each; every sentence lists the claims it rests on (`claim_ids`), and `unverified` names the criteria we could not check. |
| `commerce` | Offers and prices; `no_offer` products come with a store-search link instead. |
| `sources` | Every source used: `url`, `title`, the text we read, and when we read it (`fetched_at`). `site` is a web page's domain and `community` is a subreddit. `data_class` is `reddit` or `public`. Reddit sources can also carry `upvotes`, `comment_count` and `comments`. |
| `stage_reports` | One per stage: `ok`, `fallback`, `skipped`, `timeout` or `failed`, with `duration_ms`. |
| `partial` | `true` if the 150-second search limit cut the search short. Show what is there and say so. |
| `coverage_note` | Plain words about what was missing (for example a source that was unavailable). Always set when `partial` is true or a stage did not finish normally. It also carries the sample label (below). Show it. |
| `saved` | `true` only when the result's row was committed to the database (`public.searches`). `false` when the database is off, down, slow or failing: show "This result isn't saved." The rest of the result is the same either way. |

**Sample label:** while any part of a result comes from a placeholder stage, `coverage_note` ends with "These are
sample results while live research is being built. They are not real recommendations." The label follows the code
that actually ran: a real stage gives no label, and a placeholder running as a real stage's fallback does. If the
note would pass 400 characters, the earlier text is cut, never this sentence. Show the note as it is; don't match
on the sentence.

**Time:** the search stops itself after 150 seconds (`SEARCH_TIME_LIMIT_S`), and that includes saving the result: the
stages stop early enough to leave the save its time (`DB_TIMEOUT_SECONDS`, 2 seconds by default). Each stage also
has its own limit, and a stage that runs out falls back instead of failing. The frontend gives up after 170 seconds
(`RESEARCH_TIMEOUT_MS`), which leaves room for the network.

**Errors:**

| Status | When | What to show |
|---|---|---|
| 422 | The body is not a valid `SearchRequest` (the message names the field; it never repeats what was typed) | `detail`, and a way back to the brief |
| network error or no answer after 170 s | The backend is down or too slow | "We couldn't finish the research. Please try again." |

A stage that fails never makes the search fail: it falls back, and `stage_reports` and `coverage_note` say so. A
save that fails never does either: the answer is the same result with `saved` set to `false`.

**Try it** (Git Bash, backend running). First run
[A whole interview in one go](api/presearch.md#a-whole-interview-in-one-go-git-bash) in the same terminal: it sets
`SID` and `RUBRIC`.

```bash
curl -s -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d "{\"session_id\": \"$SID\", \"user_id\": null, \"raw_query\": \"best laptop for coding under 70k\", \"rubric\": $RUBRIC}"
```

## Sprint 2 contracts

These types are agreed and exported now, so screens can be built against them. The work behind them lands in later
Sprint 2 stories. `POST /api/search` stays exactly as it is above.

### Rough answers (US-08)

An interview reply can carry one of these as well as `reply_text`, never both (both together answer 422):

| Field | Type | Meaning |
|---|---|---|
| `level` | `low`, `medium`, `high` or `good_enough` | A rough answer from the buttons |
| `exact_weight` | number, 0 to 10 | An exact answer from the exact-value switch |

The weight for each level is in `shared/contracts/examples/rough_levels.json` (low 2, medium 5, high 8, good enough 3). Read
it from there and never copy the numbers.

**The backend acts on these (US-08).** Send `level` or `exact_weight` with `reply_text`, never both. A level takes its
weight from the table, so `good_enough` is 3, and an `exact_weight` is used as it is. To switch an earlier answer between
rough and exact, call `interview/back` for that question, then reply with the other field. Every question's
`quick_replies` now include `Good enough`. The full rules are in [the pre-search API](api/presearch.md).

Examples: `reply_rough.json`, `reply_exact.json`.

### Search runs and live progress (P-07)

The routes are not served yet: a later Sprint 2 task builds them. These are the shapes they will use.

- **Starting a run** takes a `SearchRequest` and answers at once with `SearchRunStarted`: the `search_id` (`srch_` and 24
  hex characters, made by the backend). Never make one up.
- **A run's state** is a `SearchRun`:

| Field | Meaning |
|---|---|
| `search_id` | The run |
| `status` | `queued`, `running`, `done`, `partial`, `failed` or `cancelled`. Nothing cancels a run yet, so `cancelled` is reserved |
| `started_at` | `null` until the run leaves the queue; a run that failed or was cancelled early may never have started |
| `finished_at` | Set once the status is `done`, `partial`, `failed` or `cancelled` |
| `progress` | Every `ProgressEvent` so far |
| `result` | The `SearchResult`, set only when the status is `done` or `partial`. `partial` matches `result.partial` |

- **A progress event** is a `ProgressEvent`:

| Field | Meaning |
|---|---|
| `event_id` | Counts up from 1 within a run. Use it to resume after a dropped connection |
| `search_id` | The run |
| `stage` | The stage this event is about |
| `status` | `started`, `done`, `fallback` or `failed` |
| `at` | When it happened (UTC) |
| `message` | Short plain English. Show it as it is |
| `sources_found` | Sources found so far, per source type, such as `{"reddit": 12, "review_site": 3}`. Can be empty |

Never fake progress: until these routes exist, the live research service keeps `streamsProgress: false`.

Examples: `search_run_started.json`, `search_run_running.json`, `search_run_done.json`, `progress_events.json`.

### Research data

- **`Source`** reaches the frontend in `SearchResult.sources`. A source has a `site` or a `community`, never both. Each
  comment has `id`, `parent_id`, `depth` (1 to 3), `upvotes`, `text`, `quality` and `author_hash`. Example:
  `reddit_thread.json`.

The frontend doesn't receive these two yet. They are listed so the shapes aren't a surprise later.

- **`Claim`** now has `source_url`, `product_mention` (was `product_raw_name`), `is_product_type` and `extracted_at`, and
  keeps `retrieved_at`. A claim has exactly one of `value` (text or a number) and `score` (0 to 10). `claim_type` is
  `experience` or `spec`. Example: `product_type_claim.json`.
- **`RecommenderCount`** has `product_mention`, `distinct_recommenders` and `threads`, and is listed in
  `ResearchResult.recommender_counts`. Code counts these, never an LLM. Example: `recommender_counts.json`.

## What the backend cannot do yet

Found when the frontend was first connected (FE-0). Until each lands, the screen shows the gap plainly instead of
inventing data.

| Gap | Today | When it is planned |
|---|---|---|
| Real search results | `POST /api/search` still runs the US-52 stub stages: every search returns the same example laptops, whose criteria differ from the session's rubric. `coverage_note` says they are samples. | US-12 and US-14 to US-18, Sprints 2 and 3 |
| Live progress | No progress stream; the answer comes once, at the end. Never fake progress. The [contracts](#search-runs-and-live-progress-p-07) are ready; the routes are not. | Progress over Server-Sent Events (`ProgressEvent`), Sprint 2 |
| Every source of a disputed value | `SearchResult` has no claims list. | The evidence route (US-21), Sprint 3 |
| Typed edits to must-haves and leave-outs | `PUT /intent` refuses them with a plain 422. | US-09, US-10 and the rubric editor, Sprint 3 |
| Sessions live in the server's memory | A backend restart in the middle of a search shows "session expired". | Not planned yet: raised for Sprint 2 planning |
| A running search can't be cancelled | Leaving the page does not stop the work. `SearchRun` has a `cancelled` status, but nothing sets it. | Not planned yet: raised for Sprint 2 planning |
| A filtered product may be missing from `products` | The page has no name to show for it; show the product ID's fallback text. | Not planned yet: raised for Sprint 2 planning |
