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
  `DELETE`, and only the `Content-Type`, `Last-Event-ID` and `Authorization` headers. The frontend may read the
  `Retry-After` header.
- **Signed in:** send the Supabase access token on every call, as `Authorization: Bearer <access_token>` ([signing
  in](#signing-in-us-30)). Without one, the shopper is a guest.
- **Errors** are always `{"detail": "<one plain sentence>"}`, never a stack trace. Show `detail` as it is.
- **Types:** every request and response is a contract model. The JSON Schemas are in `shared/contracts/schema/`,
  and the TypeScript types are generated from them into `frontend/src/types/contracts/` (`npm ci` does it). Never
  write these types by hand.

## Every route the backend serves

These twenty routes are the whole API, and they match the backend's OpenAPI (`GET /openapi.json`). `GET /api/health`
is for `scripts/dev.py`, to see that the backend is up. The screens do not call `POST /api/search` (they use search runs).
The brief page's rubric editor (S3-14) calls `POST /api/presearch/{session_id}/rubric` to save the shopper's edits and
`POST /api/presearch/exclusions/parse` to read leave-outs from their words.

| Route | What it does | Request body | Response 200 | Errors | Details |
|---|---|---|---|---|---|
| `GET /api/health` | Is the backend up; is the database `ok`, `off` or `down` | none | `{"status", "schema_version", "database"}` | none | [presearch.md](api/presearch.md#get-apihealth) |
| `POST /api/presearch/intake` | Reads the shopper's request; starts a session | `text` (up to 1,000 characters; blank text gets a follow-up question); `session_id` (optional) | `session_id`, `intent`, `ready_for_research` | 404, 422 | [presearch.md](api/presearch.md#post-apipresearchintake) |
| `PUT /api/presearch/{session_id}/intent` | Changes what we understood (the use, budget or region) | `changes` (the `Intent` fields to change; a new product, must-have or leave-out is refused with 422) | the session's new `Intent` | 404, 409, 422 | [presearch.md](api/presearch.md#put-apipresearchsession_idintent) |
| `POST /api/presearch/{session_id}/criteria` | The criteria to compare | none | `CriteriaSet` | 404, 409 | [presearch.md](api/presearch.md#post-apipresearchsession_idcriteria) |
| `POST /api/presearch/{session_id}/interview/start` | The first question, in the [smart order](#the-interviews-order-us-04) | none | `InterviewStep` | 404, 409 | [presearch.md](api/presearch.md#post-apipresearchsession_idinterviewstart) |
| `POST /api/presearch/{session_id}/interview/reply` | Answers the current question | `reply_text` (1 to 500 characters); `question_id` (optional: makes a repeated reply safe); `level` or `exact_weight` (optional, never both: [rough answers](#rough-answers-us-08)) | `InterviewStep` (next question, rephrase, or finished with the rubric; it may finish before 10 questions) | 404, 409, 422 | [presearch.md](api/presearch.md#post-apipresearchsession_idinterviewreply) |
| `POST /api/presearch/{session_id}/interview/back` | Asks an earlier question again; the reply re-plans the rest | `question_id` (1 to 64 characters) | `InterviewStep` | 404, 409, 422 | [presearch.md](api/presearch.md#post-apipresearchsession_idinterviewback) |
| `POST /api/presearch/{session_id}/interview/forget` | Forgets one remembered preference in this interview | `preference_id` (a `RememberedPreference.preference_id`) | `InterviewStep` | 404, 409, 422 | [remembered preferences](#remembered-preferences-us-06) |
| `POST /api/presearch/{session_id}/rubric` | The shopper's rubric edits: switch a must-have, add or remove a leave-out, set a weight | `RubricEditRequest` | `RubricEditResponse` (the rebuilt `Rubric`) | 404, 409, 422 | [editing the rubric](#editing-the-rubric-us-09) |
| `POST /api/presearch/exclusions/parse` | Reads leave-outs from the shopper's words ("no Xiaomi"); needs no session | `text` (1 to 200 characters) | `ExclusionParseResponse` | 422 | [reading leave-outs](#reading-leave-outs-from-words-us-10) |
| `GET /api/presearch/{session_id}` | Everything about the session, for a page reload | none | the session | 404 | [presearch.md](api/presearch.md#get-apipresearchsession_id) |
| `POST /api/search` | Runs the search with the finished rubric | `SearchRequest` | `SearchResult` | 422 | below |
| `POST /api/search/runs` | Starts the same search in the background | `SearchRequest` | 202 `SearchRunStarted` | 422, 503 | [search runs](#search-runs-with-live-progress-us-12) |
| `GET /api/search/runs/{search_id}` | The run's state, and its result once it has finished | none | `SearchRun` | 404 | [search runs](#search-runs-with-live-progress-us-12) |
| `GET /api/search/runs/{search_id}/events` | The run's progress, live (Server-Sent Events) | none | `text/event-stream` | 204, 404 | [search runs](#following-its-progress) |
| `DELETE /api/search/runs/{search_id}` | Cancels the run | none | `SearchRun` (202 while it stops) | 404 | [search runs](#cancelling) |
| `GET /api/search/runs/{search_id}/evidence` | The claims behind one value of a finished run, each with its quote and source | none; query `product_id` and `criterion_key`, both required | `EvidenceResponse` | 404, 409, 422 | [sources behind a value](#sources-behind-a-value-us-21) |
| `GET /api/me` | Who is signed in: a guest without a token | none | `MeResponse` | 401, 503 | [signing in](#signing-in-us-30) |
| `GET /api/me/searches` | The signed-in shopper's saved searches, newest first, 20 a page | none; query `cursor` (optional) | `SavedSearchList` | 401, 422, 503 | [signing in](#signing-in-us-30) |
| `GET /api/me/searches/{id}` | One of the signed-in shopper's saved searches | none | `SearchResult` (`saved: true`) | 401, 404, 503 | [signing in](#signing-in-us-30) |

What each error means:

| Status | When | `detail` |
|---|---|---|
| 404 | The session is unknown or has expired | `This session has expired. Please start a new search.` |
| 404 | The search run is unknown, or ended more than 30 minutes ago | `We couldn't find that search. Finished searches are only kept for a while.` |
| 409 | A step was called out of order | Says which step comes first |
| 422 | The body is not valid | Names the field, and never repeats what was typed |
| 503 | Every search worker is busy (starting a search run only); `Retry-After` says how many seconds to wait | `ShopSense is busy with other searches right now. Please try again in a moment.` |

The evidence route has its own sentences, which say why it can't answer: see [when it can't answer](#when-it-cant-answer).

Trust this page for errors, not the OpenAPI. The OpenAPI lists 404 and 409 only for some routes (search runs and the
evidence route), and it shows 422 as FastAPI's list of errors, when the real answer is the one plain sentence above.

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
| `user_id` | string or `null` | Leave it out. The backend sets it from the bearer token ([signing in](#signing-in-us-30)): the signed-in shopper's id, or `null` for a guest. A guest's search is stored only under the anonymous `session_id`, and is never linked to an account. `POST /api/search` answers it as set; a search run's result always shows `null`. |
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

**Sample label:** when example data reached a result, `coverage_note` ends with "These are sample results while live
research is being built. They are not real recommendations." The label follows the code that actually ran: a real
stage gives no label; a stub's own output does; and a fallback does only when it returns a stub's example data (in a
live search today, only decide's example ranking). The enrich stub, which adds nothing, and a stub fallback that
returns nothing, such as research's one query and no sources, never label it (S4-34, S4-35). If the note would pass
400 characters, the earlier text is cut, never this sentence. Show the note as it is; don't match on the sentence.

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
| `GET /api/search/runs/{search_id}/evidence?product_id=…&criterion_key=…` | **200** `EvidenceResponse`: see [sources behind a value](#sources-behind-a-value-us-21) |

An unknown `search_id`, or one whose run ended more than 30 minutes ago (or was pushed out by newer ones; see
[how long a run is kept](#how-long-a-run-is-kept)), answers **404** on every route (the evidence route says which of
the two it is). Every finished run's result is saved, as `POST /api/search` saves it.

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
- **`sources_found`** lists every source type. Live research counts the sources it found; if research falls back to
  samples, they are not counted and the counts stay 0.

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
It leaves memory within 10 minutes of that, even if nobody asks for it again.
That holds even while a progress stream is still open on it, because its result holds Reddit text (D-53). Such a stream
then sends the final `run` event, if it hasn't yet, with the result as it is saved: Reddit's words are replaced by
`[Reddit text is not kept]`. Then it closes. Only a client that stopped reading, or opened the stream, just as the keep
time ran out sees this. If even that final event can't be made, the stream closes without one, and a reconnect gets 404.
The keep time runs out on the wall clock too, so a backend on a machine that slept doesn't keep a run longer.
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

## Sources behind a value (US-21)

`GET /api/search/runs/{search_id}/evidence?product_id=…&criterion_key=…` answers the claims behind one value of a
finished search run, each with its exact words and its source, as an `EvidenceResponse`
(`shared/contracts/schema/EvidenceResponse.json`). "Where this comes from" shows it. Both query parameters are required:
`product_id` is one of the result's products, and `criterion_key` is a criterion or hard-rule attribute that product
has a value for (`products[].values[].criterion_key`).

- **`state`** is the value's state, and **`winning_claim_ids`** are the claims it rests on.
- **`claims`** lists those claims first, then the ones that disagree:
  - `known`: its claims, and any that disagreed;
  - `disputed`: both sides; the claims not in `winning_claim_ids` are the other side;
  - `estimated`: a peer average has no claims of its own, so `winning_claim_ids` is empty, and `claims` holds only
    the claims that conflicted, if there were any;
  - `unknown`: none.
- **Each claim** has its exact `quote`; its `value` (with `unit`) or its `score`, never both; `polarity`, `claim_type`
  and `retrieved_at`; and its `source`: `id`, `url`, `name` (the site's domain, or the community, such as `r/...`),
  `type`, `tier` and `fetched_at`.
- **Reddit's words are here.** The saved result drops them (D-53): once saved, a Reddit claim's `citations[].quote` is
  `null`. The run keeps its claims in memory only, for as long as it keeps the run (`SEARCH_RUN_KEEP_MINUTES`: 30 by
  default, never more than 120, far less than the 24 hours D-53 allows), so this route still has them.
  `quote_withheld_reason` is set exactly when a quote is `null`, which a kept run never gives today.
- **What is kept:** only the claims the result's values cite. They are never in `GET /api/search/runs/{search_id}` or
  its stream, never saved, and they go with the run (a backend restart loses them too). When this route can't answer,
  the frontend shows the result's own `citations` instead.

```bash
curl -s "http://localhost:8000/api/search/runs/$RUN/evidence?product_id=prd_kestrel_slim_15&criterion_key=battery_life"
```

Response 200 for a disputed value, from the sample stages (the tests run these):

```json
{
  "search_id": "srch_665f0028e3c2fccbb0a29523",
  "product_id": "prd_kestrel_slim_15",
  "criterion_key": "battery_life",
  "state": "disputed",
  "winning_claim_ids": ["clm_2c31271c5c981d18"],
  "claims": [
    {
      "claim_id": "clm_2c31271c5c981d18",
      "quote": "battery rated up to 10 hours",
      "value": 10.0,
      "unit": null,
      "score": null,
      "polarity": "neutral",
      "claim_type": "spec",
      "retrieved_at": "2026-10-03T10:00:00Z",
      "source": {
        "id": "src_2958fd9d1b3643cf",
        "url": "https://kestrel.example.com/slim-15/specs",
        "name": "kestrel.example.com",
        "type": "manufacturer",
        "tier": 1,
        "fetched_at": "2026-10-03T10:00:00Z"
      }
    },
    {
      "claim_id": "clm_46feb890769b8225",
      "quote": "I get about 6 hours, not 10",
      "value": 6.0,
      "unit": null,
      "score": null,
      "polarity": "neutral",
      "claim_type": "experience",
      "retrieved_at": "2026-10-03T10:00:00Z",
      "source": {
        "id": "src_edb2d0c42af72797",
        "url": "https://www.reddit.com/r/developersIndia/comments/abc123/",
        "name": "r/developersIndia",
        "type": "reddit",
        "tier": 4,
        "fetched_at": "2026-10-03T10:00:00Z"
      }
    }
  ],
  "quote_withheld_reason": null
}
```

And for a known value with one source (`product_id=prd_nova_book_14&criterion_key=cpu_performance`):

```json
{
  "search_id": "srch_665f0028e3c2fccbb0a29523",
  "product_id": "prd_nova_book_14",
  "criterion_key": "cpu_performance",
  "state": "known",
  "winning_claim_ids": ["clm_053f4bf83174cf53"],
  "claims": [
    {
      "claim_id": "clm_053f4bf83174cf53",
      "quote": "Nova Book 14: cpu performance rated 7.5/10.",
      "value": null,
      "unit": null,
      "score": 7.5,
      "polarity": "positive",
      "claim_type": "experience",
      "retrieved_at": "2026-10-03T10:00:00Z",
      "source": {
        "id": "src_37c43827cfe41e89",
        "url": "https://example-reviews.in/kestrel-slim-15-review",
        "name": "example-reviews.in",
        "type": "review_site",
        "tier": 3,
        "fetched_at": "2026-10-03T10:00:00Z"
      }
    }
  ],
  "quote_withheld_reason": null
}
```

### When it can't answer

Every error is `{"detail": "<one plain sentence>"}`. Show `detail`, or fall back to the result's `citations`.

| Status | When | `detail` |
|---|---|---|
| 404 | The server has no record of this run: it never existed, the backend restarted since, or it was dropped so long ago that the server forgot it (after 1,000 newer drops) | `We couldn't find that search.` |
| 404 | The run has been dropped: it ended more than `SEARCH_RUN_KEEP_MINUTES` ago, or newer runs pushed it out. A signed-in shopper's run leaves no such trace, so its ID is never confirmed: it gets the line above, even for its owner. | `This search has expired. Run it again to see its sources.` |
| 404 | The run was cancelled or failed, so it has no result | `This search has no results to show sources for.` |
| 404 | `product_id` is not one of the result's products | `This search didn't keep that product.` |
| 404 | That product has no value for `criterion_key` | `That product has no value for that criterion.` |
| 404 | The claims behind the value were not kept, so there is nothing valid to show | `The sources for this value are no longer kept.` |
| 409 | The run is still `queued` or `running` | `This search hasn't finished yet.` |
| 422 | `product_id` or `criterion_key` is missing | Names the parameter, such as `Please check: query.criterion_key - Field required.` |

## The interview's order (US-04)

The interview asks one question at a time, and it works the order out again after every answer (US-04). Each
question is about the criterion that matters most and that the app is least sure of. With today's tuned numbers
(D-75), every criterion not asked yet is equally unsure, so this follows the category's usual order. The topic and
budget rules can still leave a question out, and going back or correcting an answer re-plans the rest.

- **At most 10 questions.** It may finish sooner: once the 3 criteria that matter most are settled, the next step is
  `finished` with the rubric. With today's numbers that happens only when those 3 were all answered "good
  enough". Treat `questions_asked` as the count so far, never as a fixed total.
- **`ordering`** on every step says how its question was chosen: `information_gain` normally; `fixed_order` for a
  turn where the smart order failed and the usual order was used instead. The next turn tries the smart order again.
- **`state.beliefs`** holds what the app believes about each weight: a `mean` (0 to 10) and an `sd` (how unsure). It
  is worked out again from the answers on every turn, so going back or correcting an answer keeps it right. It is
  empty (`{}`) on a `fixed_order` turn. Screens don't need to show it.
- **`question.why_asking`** says in plain words why the question comes now: the criterion usually matters and the
  shopper's view isn't known yet. The criterion's definition follows when it fits in 200 characters. Show it under
  "Why this question?". A question asked in the usual order (on a `fixed_order` turn) gives the definition alone.
- **Going back** (`interview/back`) or a correction re-plans what follows. A finished interview can open again this
  way, and one that had stopped early can ask more.
- **Rubric edits** (`POST /rubric`, below) never change the beliefs, because an edit is not an answer. They stay
  applied whatever the interview asks next, and they survive starting the interview again.

Every step keeps the `InterviewStep` shape described in [api/presearch.md](api/presearch.md).

## Remembered preferences (US-06)

For a signed-in shopper (the request carries their token), the interview starts with up to 5 preferences remembered
from their earlier searches. A guest's interview never does, and a guest's answers are never kept (D-36).

- **Where they are.** `state.remembered`, on every step, the best first. Each is a `RememberedPreference`:
  `preference_id`, `kind`, `text` (plain words, such as "Battery life: high importance"), `criterion_key`, `weight`
  (weights only), `similarity` (0 to 1: how close it is to this search's criterion; 1 for a leave-out marked Always)
  and `age_days`.
- **A remembered weight is already answered.** It is in `state.history` with `status: "remembered"`, and its
  question's `why_asking` says it was remembered. The planner never asks it, and it doesn't count in
  `questions_asked`. In the rubric its `origin` is `remembered`. Show it as "Remembered", with two buttons:
  - **Change:** `POST /interview/back` with its `question_id` asks it again, and the reply is an ordinary answer.
    Going back to another question first leaves it remembered. The rubric editor's `set_weight` changes it too.
  - **Forget:** `POST /interview/forget`, below. The criterion is then asked like any other.
- **A remembered must-have** is in the rubric's `hard_rules`, like the request's own, and `switch_rule` switches it.
  Its `text` is "Must have: " and the rule's own words (the rule's `source_text`); `criterion_key` names the criterion
  it is about, if any.
- **A remembered leave-out** (marked Always) is in the rubric's `exclusions` with `always: true`, and
  `remove_exclusion` removes it. Its `text` is "Never " and the value, with the kind in brackets for a model, feature
  or seller ("Never Acme Book 14 (model)"). A brand left out with Always comes back in every category; a model,
  feature or seller only in its own.
- **Never twice.** A must-have or leave-out the request already has, in the same words, isn't added again, and
  neither is a leave-out the shopper removed in this session.
- **Answers first.** The 5 places go to remembered weights and must-haves first. Leave-outs fill the places left,
  so a shopper's many Always leave-outs never push their remembered answers out.
- **For the whole session.** What the shopper forgets, or removes from the rubric, stays out for the rest of the
  session, a restart of the interview included. A restart keeps what was remembered marked as remembered, and keeps
  a remembered must-have switched off if the shopper switched it off.
- **Saved when the rubric is final.** When the interview finishes, and again after each rubric edit on a finished
  interview, the shopper's lasting preferences are saved, after the reply has gone:
  - weights they answered, kept from memory, or set in the rubric editor. Never skipped ones, ones left at the usual
    weight, ones a topic rule settled, or a reply the app couldn't read and counted at the usual weight;
  - must-haves they kept on (never a budget or a rule on the price);
  - leave-outs marked Always.

  Saving the same one again updates it. Only the shopper who started the interview is saved for: if a guest signs in
  half way, nothing is saved.
- **Older counts for less.** A preference counts half as much every 90 days, and one that scores under 0.7 isn't used.
  So a remembered answer unused for about 46 days is asked again. Each use saves it again, which keeps it fresh.
- **When memory can't help,** `remembered` is empty and the interview is exactly as it was before US-06, with nothing
  shown as an error. That happens when memory is off (`MEMORY=off`), there is no database, the model isn't ready
  within 1.5 seconds, or anything else fails.

### POST /api/presearch/{session_id}/interview/forget

Body: `{"preference_id": "<uuid>"}`. Answers the interview's next `InterviewStep`: the current question again, a new
one (the forgotten weight's, if it now comes next), or the finished rubric.

- It forgets for this session: a restart of the interview doesn't bring it back. Deleting a preference from memory is
  the memory screen's job (US-11).
- 404, `That isn't one of the remembered preferences in this interview.`, when the interview holds no remembered
  preference with that id.
- 409 before the interview starts.

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
| `add_exclusion` | `exclusion` (`kind`, `value`, `always`) | Adds a leave-out; adding one the shopper removed brings it back. Adding one the request itself named, with a different `always`, sets "Always" on it |
| `remove_exclusion` | `exclusion` | Removes a leave-out, whether the request named it or the shopper added it. Matched on `kind` and `value`, ignoring case |
| `set_weight` | `criterion_key`, `weight` (0 to 10) | Sets that criterion's weight, with `origin: "manual"` |

The edits are stored on the session, so every later rebuild applies them last: a new answer and going back to a
question keep them, and so does "Always" on a leave-out the request itself named (S4-17). So does a changed intent: after `PUT /intent` (and `POST /criteria` when the use changed), start
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
  },
  "note": null
}
```

- **Every weight 0:** when the edits leave every weight at 0, the rubric uses the category's usual weights (`origin:
  "default"`), and the response's `note` holds the sentence to show: `Every weight was set to 0, so the usual weights
  are used until you raise one.` Otherwise `note` is `null`. The edits stay stored, so raising one weight counts again.
  - **After a reload,** `GET /api/presearch/{session_id}` answers the same sentence in `PresearchSession.note` while
    the edits leave every weight at 0, and `null` otherwise (1.10.0). It is worked out on each read, never stored.
  - **The header** `X-Rubric-Note` (exposed to the browser through CORS) stays on both routes for one more sprint
    (S5-0), so a screen may read either; read `note` from now on.
- **"Always" on the request's own leave-out:** an `add_exclusion` edit for a leave-out the request itself named (with
  `always` changed) is stored in `InterviewState.manual_always`, apart from the shopper's added leave-outs, so it
  counts once against the 30 (1.10.0). The rubric shows it in its place, with the flag.
- **Errors:**
  - 404: the session has expired.
  - 409: the interview hasn't started (`The interview hasn't started yet. Please start it first.`).
  - 422: an unknown rule or criterion, with its name, such as `No criterion is called oops.` or
    `No hard rule has the id …`. Nothing is stored then.
  - 422: an edit with the wrong fields, with the field named.
- **What else changes:** if the interview has finished, the session's stored `interview_step.rubric` follows the edits,
  so a page reload shows them. After `PUT /intent`, `GET /api/presearch/{session_id}` may show an `interview` whose
  `history` is empty and an `interview_step` of `null`: the edits are waiting for the interview to start again.
- **Who sends them:** the brief page's rubric editor (S3-14) sends these edits, and decide filters on the active hard
  rules only (S3-10): a rule switched to a preference no longer removes a product.

## Reading leave-outs from words (US-10)

`POST /api/presearch/exclusions/parse` reads what the shopper typed into the leave-out box and answers the exclusions
it found, plus anything it could not read as one. It needs no session and changes nothing: the rubric editor (S3-14)
then adds the exclusions to the session through the rubric route. Code reads the words; no AI model is asked.

- **Phrases:** "no", "not", "without", "except", "avoid", "anything but" and "don't want" start a leave-out, in any
  case, and lists split on "or", "and", "nor", commas and "/". A bare list such as `Xiaomi, Realme` is read too.
- **Kinds:**
  - `brand`: one of the brands ShopSense knows (`config/reddit_quality.yaml`).
  - `model`: a name with a digit, such as `Galaxy A35` or `Pixel 8`.
  - `feature`: a feature in `config/exclusions.yaml` (`touchscreen`, `curved display`, `notch`, `glossy screen` and
    others), in any of the ways it is written there.
- **Not understood:** anything else comes back in `not_understood`, as typed, and is never guessed. So does a measure
  (`no more than 1.5 kg` is a must-have, not a leave-out). The screen should show those words, so the shopper can
  rephrase them.
- **`always`** is always `false` here. The shopper sets it in the editor, and it is stored with the session's leave-outs.
  A guest's `always` is never used in another search: saving it to memory needs login (US-30, Sprint 4; D-36).
- **Errors:** 422 when `text` is blank or over 200 characters, naming the field.

Example request:

```json
{"text": "No Xiaomi or Realme, not touchscreen, and avoid the Galaxy A35 or Zorblax"}
```

Response 200 (`ExclusionParseResponse`):

```json
{
  "exclusions": [
    {"kind": "brand", "value": "Xiaomi", "always": false},
    {"kind": "brand", "value": "Realme", "always": false},
    {"kind": "feature", "value": "touchscreen", "always": false},
    {"kind": "model", "value": "Galaxy A35", "always": false}
  ],
  "not_understood": ["Zorblax"]
}
```

The same reader runs in intake's keyword rules, so "best phone under 30k, no Xiaomi" leaves Xiaomi out even when no AI
model answers. There it reads only phrases that start with one of the words above.

## Signing in (US-30)

The shopper signs in with Google through Supabase Auth, in the browser, using the project's publishable key (D-32).
The backend never sees a password or a key: it reads the access token the browser sends and checks it against the
project's public signing keys ([api-setup.md](api-setup.md#supabase-auth-checking-a-sign-in-us-30-p-24)).

- **The header:** `Authorization: Bearer <access_token>`, the session's `access_token` from Supabase. Send it on every
  call while the shopper is signed in. Supabase refreshes it about once an hour; send the current one.
- **Every route takes it.** A signed-in shopper's request (`POST /api/presearch/intake`) and finished search
  (`POST /api/search`, `POST /api/search/runs`) are saved with their account. The backend sets `SearchRequest.user_id`
  from the token itself and ignores whatever the body says, so leave it out.
- **A signed-in shopper's search run is theirs alone.** Send the same token on `GET /api/search/runs/{search_id}`,
  its `/events` stream, its `/evidence` and `DELETE`. Without it, or with another account's, they answer the same 404
  as an unknown search, never 403. No run state or event ever includes `user_id`. A guest's run works as before, for
  anyone with its ID. A browser `EventSource` can't send headers, so read a signed-in run's events with `fetch` and the
  header.
- **No token, or a bad one, is a guest** on every route but the three below: the search runs, and is saved under its
  session ID only. A guest's searches are never linked to an account later, even after signing in (D-36).

### GET /api/me

Who the browser is signed in as. `MeResponse`:

- No token: `{"signed_in": false, "user_id": null, "display_name": null}`. No database is needed.
- A good token: `{"signed_in": true, "user_id": "<uuid>", "display_name": null}`. The first call after signing in
  makes the shopper's profile; `display_name` stays `null` until they set one.

### GET /api/me/searches

The signed-in shopper's saved searches, newest first, 20 a page. `SavedSearchList`:

```json
{
  "items": [
    {"id": "<uuid>", "created_at": "2026-11-20T08:05:00Z", "request_text": "best laptop for coding under 70k",
     "category": "laptop", "top_product": "Nova Book 14"}
  ],
  "next_cursor": "eyJ0Ijoi..."
}
```

- **The next page:** `GET /api/me/searches?cursor=<next_cursor>`. `next_cursor` is `null` on the last page. Treat it
  as opaque; one this route didn't make answers 422.
- **`request_text`** is at most 500 characters: a longer request (up to 1,000) ends with "…". Open the search for
  the whole text (`result.request.raw_query`).
- **`top_product`** is the first-ranked product's name, and `null` when the ranking scored nothing.

### GET /api/me/searches/{id}

One saved search, as the `SearchResult` it finished with, with `saved: true`. Only the owner gets it. Anyone else's
id, a guest's, or one that doesn't exist all answer the same 404, so an id is never confirmed. Reddit's words are not
in it: a saved result never keeps them (D-53).

### Errors on these three routes

| Status | When | `detail` |
|---|---|---|
| 401 | The token is expired, from another project, or not valid; also `/api/me/searches` without one. Sign in again. The response has `WWW-Authenticate: Bearer`. | `Your sign-in has expired or isn't valid. Please sign in again.` (no token: `Please sign in to see your saved searches.`) |
| 404 | That saved search isn't this shopper's, or doesn't exist | `We couldn't find that saved search.` |
| 422 | The `cursor` wasn't made by this route | `That page link doesn't work. Please start again from the first page.` |
| 503 | The database is off or down. Searches still work. | `Your account can't be reached right now. Searching still works; please try again in a minute.` |
| 503 | Sign-in can't be checked (the backend's `SUPABASE_URL` is unset, or the signing keys can't be fetched) | `We can't check sign-ins right now. Please try again in a minute.` |

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

Never fake progress: the research screen starts a run (`POST /api/search/runs`), follows its events and cancels it with
`DELETE`; it shows only the progress the backend sends (task #27).

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

## Sprint 5 contracts (1.10.0)

Agreed and exported in S5-0 (D-23, P-40, P-42), so screens can be built against them. The work behind most of them
lands in later Sprint 5 stories; until then each is empty or `false`. A saved result from before 1.10.0 still loads:
the backend fills every new field with its default when it sends it.

| Field or type | Story | What it holds |
|---|---|---|
| `PreferenceItem` | US-06, US-11 | One remembered item: `id`, `kind` (`weight`, `hard_rule` or `exclusion`), `category` (`null` exactly for a brand left out with Always, which holds everywhere), `criterion_key`, `text` (plain words, at most 200 characters), `weight` (0 to 10), `rule` (a `HardRule`), `exclusion` (an `Exclusion`), `always`, `created_at`, `updated_at`. A weight carries `criterion_key` and `weight`; a must-have `rule`; a leave-out `exclusion`; nothing else. No embedding, user id or email |
| `PreferenceList` | US-11 | `items`, newest first. Example: `preference_list.json` |
| `PreferenceEdit` | US-11 | A request body: `text`, `weight`, `active` (a must-have: `false` makes it a preference) and `always`, each optional; at least one. Example: `preference_edit.json` |
| `PreferenceExport` | US-11 | `exported_at` and every item. Example: `preference_export.json` |
| `RememberedPreference.preference_id`, `.kind` | US-06 | The remembered answer's item and kind, so the interview can change or forget it in one click |
| `SearchResult.cached`, `.cached_at` | US-34 | `true` and the time the result was made when it came from the search cache; else `false` and `null` |
| `DecideResult.checked_values` | US-23 | What the filter read for every product decided, shown, can't tell or filtered out: `price_inr` and each hard rule's attribute (every rule, on or off), each a `CheckedValue` (below) |
| `RankedProduct.rule_notes` | US-09 | Up to 5 sentences on how a RAM or storage must-have passed through one version of the product, such as `It meets "at least 8 GB RAM" in its 8 GB version`. Empty when no version decided a rule |
| `PresearchSession.note` | US-09 | The all-zero sentence on a reload (above) |
| `InterviewState.manual_always` | US-10 | The shopper's Always on the request's own leave-outs (above) |

- **Re-rank with no server call (US-23):** for the products already shown, a ranked product's `contributions` and the
  rubric already give its score, so nothing was added for re-weighting.
  - **The score:** `min(100, Σ score × share × 10)`, over the criteria whose `score` isn't `null`, with each criterion's
    share of the weight renormalised over those criteria. If those weights add up to 0, every one of them gets an
    equal share.
  - **What it doesn't cover:** re-scoring after a re-filter. A product the browser lets back in has no contributions.
    Compared and estimated scores depend on which products are shown. Leave-outs (brand families, features) aren't in
    `checked_values`. These are gaps for the US-23 owner to settle.
- **`CheckedValue`:** `product_id`, `attribute`, `state`, `values`, `unit`, `any_version` and `from_names`.
  - **`state`** is as the filter read it: `unknown` when verify gave no value, and `known` for a value disputed in
    scores only, which the filter checks as an agreed one.
  - **`values`** holds what the filter compared:
    - a price in rupees (`unit` `INR`);
    - a size as a number in the first rule's unit (`gb`, `g`, ...), read as decide reads it (a bare number takes the
      attribute's unit; words around a size count for "at least" and "at most" rules only);
    - else the value as its claims gave it.
  - **Versions** (`any_version: true`) come claims' first. `from_names` says how many of the last ones only a product's
    name states.
  - **To keep a rule as decide does:**
    - **No values:** can't tell.
    - **One value:** the rule keeps it or not.
    - **Several, not versions (a disputed value's sides):** every side keeps it, pass; every side breaks it, fail; else
      can't tell. A disputed value with one side alone can't tell either.
    - **Versions:** any one keeping it, pass. Otherwise, if one can't be compared, or the state is `disputed` and only
      one value came from the claims, can't tell. Else fail.

## What the backend cannot do yet

Found when the frontend was first connected (FE-0). Until each lands, the screen shows the gap plainly instead of
inventing data.

| Gap | Today | When it is planned |
|---|---|---|
| Real search results | `POST /api/search` and search runs now run live research, extract, match, verify, decide, explain and commerce, so the products, values, ranking, explanations and prices are real. A result is labelled a sample (the sample sentence in `coverage_note`) only when example data reached it: a stub's own output, or a fallback that returns a stub's example data (in a live search today, only decide's example ranking). The enrich stub, which adds nothing, and a stub fallback that returns nothing, such as research's one query and no sources, never label it (S4-34, S4-35). | US-41 (enrich), US-22 (checked explanations) |
| Every source of a disputed value | Done: the [evidence route](#sources-behind-a-value-us-21) gives both sides of a value, with each claim's quote and source, while the run is kept. After that, only the result's `citations` remain, without Reddit's words. | Done (US-21, S3-11) |
| Typed edits to must-haves and leave-outs | `PUT /intent` still refuses them with a plain 422; the rubric route switches a must-have, adds or removes a leave-out and sets a weight ([editing the rubric](#editing-the-rubric-us-09)). Leave-outs read from typed words come with US-10. | US-10 and the rubric editor (S3-14), Sprint 3 |
| Sessions live in the server's memory | A backend restart in the middle of a search shows "session expired". | Not planned yet: raised for Sprint 2 planning |
| A filtered product may be missing from `products` | The page has no name to show for it; show the product ID's fallback text. | Not planned yet: raised for Sprint 2 planning |
