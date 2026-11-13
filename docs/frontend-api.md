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
- **JSON in, JSON out.** Send `Content-Type: application/json`. The backend allows only `GET`, `POST`, `PUT` and
  `DELETE`, and only the `Content-Type` and `Last-Event-ID` headers. The frontend may read the `Retry-After` header.
- **Errors** are always `{"detail": "<one plain sentence>"}`, never a stack trace. Show `detail` as it is.
- **Types:** every request and response is a contract model. The JSON Schemas are in `shared/contracts/schema/`,
  and the TypeScript types are generated from them into `frontend/src/types/contracts/` (`npm ci` does it). Never
  write these types by hand.

## Every route the backend serves

These fourteen routes are the whole API, and they match the backend's OpenAPI (`GET /openapi.json`). The frontend
calls all of them except `GET /api/health`, which `scripts/dev.py` uses to see that the backend is up.

| Route | What it does | Request body | Response 200 | Errors | Details |
|---|---|---|---|---|---|
| `GET /api/health` | Is the backend up; is the database `ok`, `off` or `down` | none | `{"status", "schema_version", "database"}` | none | [presearch.md](api/presearch.md#get-apihealth) |
| `POST /api/presearch/intake` | Reads the shopper's request; starts a session | `text` (up to 1,000 characters; blank text gets a follow-up question); `session_id` (optional) | `session_id`, `intent`, `ready_for_research` | 404, 422 | [presearch.md](api/presearch.md#post-apipresearchintake) |
| `PUT /api/presearch/{session_id}/intent` | Changes what we understood (the use, budget or region) | `changes` (the `Intent` fields to change; a new product, must-have or leave-out is refused with 422) | the session's new `Intent` | 404, 409, 422 | [presearch.md](api/presearch.md#put-apipresearchsession_idintent) |
| `POST /api/presearch/{session_id}/criteria` | The criteria to compare | none | `CriteriaSet` | 404, 409 | [presearch.md](api/presearch.md#post-apipresearchsession_idcriteria) |
| `POST /api/presearch/{session_id}/interview/start` | The first question | none | `InterviewStep` | 404, 409 | [presearch.md](api/presearch.md#post-apipresearchsession_idinterviewstart) |
| `POST /api/presearch/{session_id}/interview/reply` | Answers the current question | `reply_text` (1 to 500 characters); `question_id` (optional: makes a repeated reply safe); `level` or `exact_weight` (optional, never both: [rough answers](#rough-answers-us-08)) | `InterviewStep` (next question, rephrase, or finished with the rubric) | 404, 409, 422 | [presearch.md](api/presearch.md#post-apipresearchsession_idinterviewreply) |
| `POST /api/presearch/{session_id}/interview/back` | Asks an earlier question again | `question_id` (1 to 64 characters) | `InterviewStep` | 404, 409, 422 | [presearch.md](api/presearch.md#post-apipresearchsession_idinterviewback) |
| `POST /api/presearch/{session_id}/rubric` | The shopper's rubric edits: switch a must-have, add or remove a leave-out, set a weight | `RubricEditRequest` | `RubricEditResponse` (the rebuilt `Rubric`) | 404, 409, 422 | [editing the rubric](#editing-the-rubric-us-09) |
| `GET /api/presearch/{session_id}` | Everything about the session, for a page reload | none | the session | 404 | [presearch.md](api/presearch.md#get-apipresearchsession_id) |
| `POST /api/search` | Runs the search with the finished rubric | `SearchRequest` | `SearchResult` | 422 | below |
| `POST /api/search/runs` | Starts the same search in the background | `SearchRequest` | 202 `SearchRunStarted` | 422, 503 | [search runs](#search-runs-with-live-progress-us-12) |
| `GET /api/search/runs/{search_id}` | The run's state, and its result once it has finished | none | `SearchRun` | 404 | [search runs](#search-runs-with-live-progress-us-12) |
| `GET /api/search/runs/{search_id}/events` | The run's progress, live (Server-Sent Events) | none | `text/event-stream` | 204, 404 | [search runs](#following-its-progress) |
| `DELETE /api/search/runs/{search_id}` | Cancels the run | none | `SearchRun` (202 while it stops) | 404 | [search runs](#cancelling) |

What each error means:

| Status | When | `detail` |
|---|---|---|
| 404 | The session is unknown or has expired | `This session has expired. Please start a new search.` |
| 404 | The search run is unknown, or ended more than 30 minutes ago | `We couldn't find that search. Finished searches are only kept for a while.` |
| 409 | A step was called out of order | Says which step comes first |
| 422 | The body is not valid | Names the field, and never repeats what was typed |
| 503 | Every search worker is busy (starting a search run only); `Retry-After` says how many seconds to wait | `ShopSense is busy with other searches right now. Please try again in a moment.` |

Trust this page for errors, not the OpenAPI. The OpenAPI lists no 404 or 409, and it shows 422 as FastAPI's list of
errors, when the real answer is the one plain sentence above.

FastAPI also serves its own pages, `GET /docs` and `GET /redoc`, which show the OpenAPI. They describe the API and are
not part of it. A path that doesn't exist answers 404 with `Not Found`, and a route called with the wrong method
answers 405 with `Method Not Allowed`, both in `detail`.

## POST /api/search

Runs every search stage (research → extract → match → verify → enrich → decide → explain → commerce) for one
finished interview and returns the ranked results. It answers once, at the end. To follow a search stage by stage, or
to cancel it, start a [search run](#search-runs-with-live-progress-us-12) instead.

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

## Search runs with live progress (US-12)

A search run is the same search as `POST /api/search`, started in the background. The backend answers at once with
the run's ID. The screen then follows the run stage by stage, can cancel it, and fetches the result when it ends. The
shapes are in [search runs and live progress](#search-runs-and-live-progress-p-07).

| Route | Answer |
|---|---|
| `POST /api/search/runs` (body: the same `SearchRequest` as `POST /api/search`) | **202** `SearchRunStarted` and a `Location` header; **503** when the backend is busy |
| `GET /api/search/runs/{search_id}` | **200** `SearchRun`. Its `result` is there once the status is `done` or `partial` |
| `GET /api/search/runs/{search_id}/events` | **200** `text/event-stream`; **204** when the client has already seen the end |
| `DELETE /api/search/runs/{search_id}` | **202** `SearchRun` while it stops; **200** and its `SearchRun`, unchanged, when it is too late to cancel |

An unknown `search_id`, or one whose run ended more than 30 minutes ago (or was pushed out by newer ones; see
[how long a run is kept](#how-long-a-run-is-kept)), answers **404** on every route. Every finished run's result is
saved, as `POST /api/search` saves it.

### Starting a run

```bash
curl -s -i -X POST http://localhost:8000/api/search/runs -H "Content-Type: application/json" -d @request.json
```

```http
HTTP/1.1 202 Accepted
location: /api/search/runs/srch_5f2c9a1e7b3d4860a1c2e3f4

{"search_id":"srch_5f2c9a1e7b3d4860a1c2e3f4"}
```

The `search_id` is `srch_` and 24 hex characters, made by the backend. Never make one up.

At most `SEARCH_RUN_WORKERS` searches (default 4) run at once. One more answers **503**, with `Retry-After` in seconds.
Show the `detail` and offer to try again:

```http
HTTP/1.1 503 Service Unavailable
retry-after: 30

{"detail":"ShopSense is busy with other searches right now. Please try again in a moment."}
```

### Following its progress

```ts
const events = new EventSource(`${baseUrl}/api/search/runs/${searchId}/events`);
events.addEventListener("progress", (e) => showStep(JSON.parse(e.data))); // a ProgressEvent
events.addEventListener("run", (e) => {
  events.close(); // the last event: the stream is over
  showRun(JSON.parse(e.data)); // a SearchRun, with its result when done or partial
});
```

```text
id: 1
event: progress
data: {"event_id":1,"search_id":"srch_5f2c9a1e7b3d4860a1c2e3f4","stage":"research","status":"started","at":"2026-11-08T10:00:00.120000Z","message":"Looking for reviews, videos and discussions.","sources_found":{"reddit":0,"youtube":0,"web_search":0,"review_site":0,"manufacturer":0,"lab":0,"retailer":0}}

: ping

id: 17
event: run
data: {"search_id":"srch_5f2c9a1e7b3d4860a1c2e3f4","status":"done","started_at":"...","finished_at":"...","progress":[...],"result":{...}}
```

- **`progress`** events carry one `ProgressEvent` each, and their `id` is its `event_id`. Each stage sends `started`, then
  one of: `done`; `fallback` (it had a problem or took too long, so a simpler way was used, and the search carries on);
  or `failed` (the search stops there, and the result is partial). A stage that never ran sends nothing. Show
  `message` as it is. A `fallback` or `failed` message names one of six fixed reasons (for example "the service was
  busy"), never an error's own text; `coverage_note` uses the same reasons.
- **`run`** is the last event: the whole `SearchRun`, the same body as `GET /api/search/runs/{search_id}`. Its `id` is the
  last progress event's plus 1, and the stream ends after it. Call `close()`, or the browser reconnects.
- **`: ping`** is a comment that `EventSource` ignores. The backend sends it after `SEARCH_RUN_PING_SECONDS` (default 15,
  never more than 15) of silence, so the connection stays open through long stages.
- **`sources_found`** lists every source type. The counts stay 0 until live research lands; today's research stage
  returns samples, which are not counted.

**Reconnecting:** the browser reconnects by itself and sends the last `id` it saw as `Last-Event-ID`. The stream then
sends only what came after it. A client that connects late gets every event so far, then the live ones. A
`Last-Event-ID` that is not one of the run's IDs is ignored, and the stream starts again from the first event, so drop
any event whose `id` you already have. After the final `run` event a reconnect answers **204**, which stops
`EventSource`.

```bash
curl -N http://localhost:8000/api/search/runs/$RUN/events                         # everything, then live
curl -N -H "Last-Event-ID: 4" http://localhost:8000/api/search/runs/$RUN/events   # only what came after event 4
```

### Cancelling

```bash
curl -s -X DELETE http://localhost:8000/api/search/runs/$RUN
```

Cancelling is cooperative. The stage that is running finishes, the run stops before the next one, and nothing is
saved. The answer is **202** with the run's state, still `queued` or `running`. The stream then sends the final `run`
event with the status `cancelled`, and the state says `cancelled`, with no `result`.

When it is too late to cancel, the answer is **200** and nothing changes. That happens when the run has already ended,
or has finished every stage and is saving its result. Its state may still say `running` for a moment; the final `run`
event then says `done` or `partial`. Show the results, not a "cancelled" message.

### How long a run is kept

A run's state stays in the backend's memory for `SEARCH_RUN_KEEP_MINUTES` (default 30, from 1 to 120) after it ends, then answers 404.
The backend keeps at most `SEARCH_RUN_MAX_KEPT` finished runs (default 500, 1 to 10,000), and drops the oldest first,
but never one that a stream is still reading.
A backend restart loses every run, like the pre-search sessions. The result is saved either way when the database is
on.

**Stopping the backend:** an open progress stream keeps uvicorn from stopping until the stream ends, which can take
as long as the search (at most 150 seconds). `python scripts/dev.py` starts uvicorn with
`--timeout-graceful-shutdown 5`, so Ctrl+C stops it in about 5 seconds even while a browser follows a run. On Windows,
dev.py ends the backend at once; elsewhere uvicorn closes open streams after 5 seconds and asks running searches to
stop before their next step, and dev.py forces it after 10. Started by hand, add the same flag:
`uvicorn app.main:app --timeout-graceful-shutdown 5`.

## Editing the rubric (US-09)

`POST /api/presearch/{session_id}/rubric` takes the shopper's own changes to the rubric and answers the rubric rebuilt
with them. The rubric has two lists that never mix (DR-08):

- **`hard_rules`**, the must-haves. They only filter, and only while `active` is true. Each keeps `source_text`, the
  shopper's own words, so the screen can show which thing they said became which rule. Identical rules share one `id`,
  and the rubric keeps one rule per `id`.
- **`weights`**, the preferences, each 0 to 10. They only change the order. Their `normalized` shares add up to exactly
  1. `origin` says where each came from: `answered`, `skipped`, `default` (the category's usual weight, for a criterion
  the interview didn't ask about) or `manual` (set here).

The body holds 1 to 20 edits, applied in order. Each edit carries exactly the fields of its kind:

| `kind` | Fields | What it does |
|---|---|---|
| `switch_rule` | `rule_id`, `active` | `false` makes the must-have a preference: it stays in `hard_rules` with `active: false` and no longer filters. `true` makes it a must-have again |
| `add_exclusion` | `exclusion` (`kind`, `value`, `always`) | Adds a leave-out; adding one the shopper removed brings it back |
| `remove_exclusion` | `exclusion` | Removes a leave-out, whether the request named it or the shopper added it. Matched on `kind` and `value`, ignoring case |
| `set_weight` | `criterion_key`, `weight` (0 to 10) | Sets that criterion's weight, with `origin: "manual"` |

The edits are stored on the session, so every later rebuild applies them last: a new answer and going back to a
question keep them. So does a changed intent: after `PUT /intent` (and `POST /criteria` when the use changed), start
the interview again and the edits are there. What is dropped: a switch for a rule the changed intent no longer has, a
weight for a criterion the new criteria don't list (kept, but unused), and every edit if the product category itself
changes, as they belong to the other category's criteria. Example request:

```json
{
  "edits": [
    {"kind": "switch_rule", "rule_id": "ram_gb_min_1c18a881e7", "active": false},
    {"kind": "set_weight", "criterion_key": "weight", "weight": 9},
    {"kind": "add_exclusion", "exclusion": {"kind": "brand", "value": "Nova"}}
  ]
}
```

Response 200 (`RubricEditResponse`; two of the seven weights left out here):

```json
{
  "rubric": {
    "category": "laptop",
    "use_case": "coding",
    "region": "IN",
    "budget": {"min": null, "max": {"amount": 70000.0, "currency": "INR"}},
    "hard_rules": [
      {"attribute": "ram_gb", "operator": ">=", "value": 16.0, "unit": "GB",
       "source_text": "at least 16 GB RAM", "id": "ram_gb_min_1c18a881e7", "active": false}
    ],
    "exclusions": [
      {"kind": "brand", "value": "Zenith", "always": false},
      {"kind": "brand", "value": "Nova", "always": false}
    ],
    "weights": [
      {"key": "cpu_performance", "weight": 8.0, "normalized": 0.17777777777777778, "origin": "default", "uncertainty": 0.5},
      {"key": "battery_life", "weight": 7.0, "normalized": 0.15555555555555556, "origin": "default", "uncertainty": 0.5},
      {"key": "weight", "weight": 9.0, "normalized": 0.20000000000000007, "origin": "manual", "uncertainty": 0.0}
    ]
  }
}
```

- **Every weight 0:** when the edits leave every weight at 0, the rubric uses the category's usual weights (`origin:
  "default"`), and the response has an `X-Rubric-Note` header (exposed to the browser through CORS) to show: `Every weight was set to 0, so the usual weights
  are used until you raise one.` The edits stay stored, so raising one weight counts again.
- **Errors:**
  - 404: the session has expired.
  - 409: the interview hasn't started (`The interview hasn't started yet. Please start it first.`).
  - 422: an unknown rule or criterion, with its name, such as `No criterion is called oops.` or
    `No hard rule has the id …`. Nothing is stored then.
  - 422: an edit with the wrong fields, with the field named.
- **What else changes:** if the interview has finished, the session's stored `interview_step.rubric` follows the edits,
  so a page reload shows them. After `PUT /intent`, `GET /api/presearch/{session_id}` may show an `interview` whose
  `history` is empty and an `interview_step` of `null`: the edits are waiting for the interview to start again.
- **Not here yet:** the editor itself is S3-14, and filtering by active rules is S3-10.

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

The shapes the [search-run routes](#search-runs-with-live-progress-us-12) use (US-12).

- **Starting a run** takes a `SearchRequest` and answers at once with `SearchRunStarted`: the `search_id` (`srch_` and 24
  hex characters, made by the backend). Never make one up.
- **A run's state** is a `SearchRun`:

| Field | Meaning |
|---|---|
| `search_id` | The run |
| `status` | `queued`, `running`, `done`, `partial`, `failed` or `cancelled`. `failed` means the backend itself went wrong; a stage that fails gives `partial` |
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
| `sources_found` | Sources found so far, per source type, such as `{"reddit": 12, "review_site": 3}`. The backend lists every type |

Never fake progress: the live research service keeps `streamsProgress: false` until the research screen moves to these
routes (task #27).

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
| Live progress | The backend streams it now ([search runs](#search-runs-with-live-progress-us-12)), but the research screen still calls `POST /api/search` and keeps `streamsProgress: false`. Never fake progress. | Task #27, Sprint 2 |
| Every source of a disputed value | `SearchResult` has no claims list. | The evidence route (US-21), Sprint 3 |
| Typed edits to must-haves and leave-outs | `PUT /intent` still refuses them with a plain 422; the rubric route switches a must-have, adds or removes a leave-out and sets a weight ([editing the rubric](#editing-the-rubric-us-09)). Leave-outs read from typed words come with US-10. | US-10 and the rubric editor (S3-14), Sprint 3 |
| Sessions live in the server's memory | A backend restart in the middle of a search shows "session expired". | Not planned yet: raised for Sprint 2 planning |
| A running search can't be cancelled from the screen | The backend can cancel a run now (`DELETE /api/search/runs/{search_id}`, US-12), but the screen doesn't start runs yet, so its stop dialog has nothing to cancel. | Task #27, Sprint 2 |
| A filtered product may be missing from `products` | The page has no name to show for it; show the product ID's fallback text. | Not planned yet: raised for Sprint 2 planning |
