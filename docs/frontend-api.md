# Backend API for the frontend

The one page to read before calling the backend from `frontend/`. It lists every route the app calls, documents
the search route in full, and says what the backend cannot do yet, so the screens never invent it.

The pre-search routes (intake, criteria, interview) have their own detailed page: [api/presearch.md](api/presearch.md).

## The basics

- **Base URL:** `VITE_API_BASE_URL` in `frontend/.env.local` (default `http://localhost:8000`). Every route is under `/api`.
  Start the backend from `backend/` with `uvicorn app.main:app --port 8000`.
- **CORS:** the backend accepts browser calls only from the origins in `FRONTEND_ORIGINS` (comma-separated, in the
  root `.env`; default `http://localhost:3000`, the frontend's dev server). `*` is never allowed. If the browser
  console shows a CORS error, add the page's origin there and restart the backend.
- **JSON in, JSON out.** Send `Content-Type: application/json`. The backend allows only `GET`, `POST` and `PUT`, and
  only the `Content-Type` header.
- **Errors** are always `{"detail": "<one plain sentence>"}`, never a stack trace. Show `detail` as it is.
- **Types:** every request and response is a contract model. The JSON Schemas are in `shared/contracts/schema/`,
  and the TypeScript types are generated from them into `frontend/src/types/contracts/` (`npm ci` does it). Never
  write these types by hand.

## Every route the frontend calls

| Route | What it does | Response | Details |
|---|---|---|---|
| `GET /api/health` | Is the backend up; is the database `ok`, `off` or `down` | `{"status", "schema_version", "database"}` | [presearch.md](api/presearch.md#get-apihealth) |
| `POST /api/presearch/intake` | Reads the shopper's request; starts a session | `session_id`, `intent`, `ready_for_research`, … | [presearch.md](api/presearch.md#post-apipresearchintake) |
| `PUT /api/presearch/{session_id}/intent` | Changes what we understood (category, budget, …) | the session's new `Intent` | [presearch.md](api/presearch.md#put-apipresearchsession_idintent) |
| `POST /api/presearch/{session_id}/criteria` | The criteria to compare | `CriteriaSet` | [presearch.md](api/presearch.md#post-apipresearchsession_idcriteria) |
| `POST /api/presearch/{session_id}/interview/start` | The first question | `InterviewStep` | [presearch.md](api/presearch.md#post-apipresearchsession_idinterviewstart) |
| `POST /api/presearch/{session_id}/interview/reply` | Answers the current question | `InterviewStep` (next question, rephrase, or finished with the rubric) | [presearch.md](api/presearch.md#post-apipresearchsession_idinterviewreply) |
| `POST /api/presearch/{session_id}/interview/back` | Asks an earlier question again | `InterviewStep` | [presearch.md](api/presearch.md#post-apipresearchsession_idinterviewback) |
| `GET /api/presearch/{session_id}` | Everything about the session, for a page reload | the session | [presearch.md](api/presearch.md#get-apipresearchsession_id) |
| `POST /api/search` | Runs the search with the finished rubric | `SearchResult` | below |

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
| `search_id`, `request`, `started_at`, `finished_at` | Identify the search; `request` echoes what was sent. |
| `products` | One `ProductSummary` per product that was looked at: the product, its resolved values and community signal. Look up names here by product ID. |
| `decision` | The ranking: `ranked` (in order, with `score`, `coverage` and `tied_with`) and `filtered` (dropped by a hard rule, with the reason). Every ranked product is in `products`. |
| `explanations` | Why each product is where it is, up to 8 sentences each; every sentence lists the claims it rests on (`claim_ids`), and `unverified` names the criteria we could not check. |
| `commerce` | Offers and prices; `no_offer` products come with a store-search link instead. |
| `sources` | Every source used: `url`, `title`, the text we read, and when we read it (`fetched_at`). |
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

**Try it** (Git Bash, backend running, `RUBRIC` holding the `rubric` JSON from a finished interview):

```bash
curl -s -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d "{\"session_id\": \"$SID\", \"user_id\": null, \"raw_query\": \"best laptop for coding under 70k\", \"rubric\": $RUBRIC}"
```

## What the backend cannot do yet

Found when the frontend was first connected (FE-0). Until each lands, the screen shows the gap plainly instead of
inventing data.

| Gap | Today | When it is planned |
|---|---|---|
| Real search results | `POST /api/search` still runs the US-52 stub stages: every search returns the same example laptops, whose criteria differ from the session's rubric. `coverage_note` says they are samples. | US-12 and US-14 to US-18, Sprints 2 and 3 |
| Live progress | No progress stream; the answer comes once, at the end. Never fake progress. | Progress over Server-Sent Events (`ProgressEvent`), Sprint 2 |
| Every source of a disputed value | `SearchResult` has no claims list. | The evidence route (US-21), Sprint 3 |
| Typed edits to must-haves and leave-outs | `PUT /intent` takes the fixed fields only. | US-09, US-10 and the rubric editor, Sprint 3 |
| `IntakeResponse` and the session have no exported schema | The frontend can only partly check these two answers. | A contract change: to be decided at the Sprint 2 contract review |
| Sessions live in the server's memory | A backend restart in the middle of a search shows "session expired". | Not planned yet: raised for Sprint 2 planning |
| A running search can't be cancelled | Leaving the page does not stop the work. | Not planned yet: raised for Sprint 2 planning |
| `interview/reply` has no idempotency key | Retrying after a timeout could answer the next question twice. | Not planned yet: raised for Sprint 2 planning |
| A filtered product may be missing from `products` | The page has no name to show for it; show the product ID's fallback text. | Not planned yet: raised for Sprint 2 planning |
