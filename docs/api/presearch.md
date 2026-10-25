# Pre-search API (US-05)

The routes the frontend calls before a search: the shopper's request → what we understood (`Intent`) → the
criteria to compare (`CriteriaSet`) → the interview, one question at a time (`InterviewStep`) → the rubric.
The rubric then goes into `POST /api/search` (US-52) as part of a `SearchRequest`.

Run the backend from `backend/`:

```bash
uvicorn app.main:app --port 8000
```

## The basics

- **Base URL:** `http://localhost:8000`. Every route is under `/api`.
- **JSON in, JSON out.** Send `Content-Type: application/json`.
- **Types:** `Intent`, `CriteriaSet`, `InterviewStep` and the rest are the contract models. Their TypeScript
  types come from `shared/contracts/schema/*.json` (see `frontend/CLAUDE.md`); never redefine them by hand.
- **Sessions:** the first `intake` call returns a `session_id` (like `sess_3f9a…`). Send it with every later
  call. A session not used for 2 hours expires; the server keeps at most 1,000 at a time.
- **Errors** are always `{"detail": "<one plain sentence>"}`, never a stack trace. Show `detail` to the user.

| Status | Meaning |
|---|---|
| 200 | It worked. |
| 404 | `This session has expired. Please start a new search.` Start again with a new `intake`. |
| 409 | A step was called out of order (the message says which step comes first). |
| 422 | The body or an edit is not valid (the message names the field). |

- **CORS:** only the origin in the `FRONTEND_ORIGIN` environment variable may call the API from a browser
  (default `http://localhost:3000`).
- **No LLM key?** Everything still works: intake uses its keyword rules and criteria use fixed sets.

## The flow

```
POST /api/presearch/intake               {"text": "best laptop for coding under 70k"}
  └─ ready_for_research false? show intent.clarification_question and its options (or, if
     intent.show_example_requests, a few example requests), then POST /intake again with the reply
     and the same session_id
POST /api/presearch/{id}/criteria
POST /api/presearch/{id}/interview/start            → first question
POST /api/presearch/{id}/interview/reply  (repeat)  → next question, a rephrase, or "finished" + rubric
POST /api/presearch/{id}/interview/back   (any time) → ask an earlier question again
```

## Routes

### GET /api/health

Checks the server is up.

- **Response 200:** `{"status": "ok", "schema_version": "1.0.0"}`

```bash
curl -s http://localhost:8000/api/health
```

### POST /api/presearch/intake

Reads a new request, or the reply to our clarification question.

- **Body:** `{"session_id": "<optional>", "text": "<1 to 1,000 characters>"}`
  - No `session_id`: a new search with a new session.
  - With the `session_id` of a session whose intent still needs clarification: `text` is the reply to our
    question (for example "smartwatch" after "Which kind of watch do you mean?"). If the reply is still
    unclear, the intent comes back with `show_example_requests: true` and no category: show a few example
    requests. A category is never guessed.
  - With the `session_id` of a session that is already clear: a fresh request in the same session (the
    criteria and interview are cleared).
- **Response 200:** `{"session_id": "sess_…", "intent": Intent, "ready_for_research": true}`
  - `ready_for_research: false` → show `intent.clarification_question` with `intent.clarification_options` as
    buttons, or example requests when `intent.show_example_requests` is true.
- **Errors:** 404 (unknown or expired `session_id`), 422 (empty or too long `text`).

```bash
curl -s -X POST http://localhost:8000/api/presearch/intake \
  -H "Content-Type: application/json" \
  -d '{"text": "best laptop for coding under 70k"}'
```

Save the `session_id` from the response; the examples below use it as `$SID`:

```bash
SID=sess_put_the_id_here
```

Answering a clarification question (here after `{"text": "watch"}`):

```bash
curl -s -X POST http://localhost:8000/api/presearch/intake \
  -H "Content-Type: application/json" \
  -d "{\"session_id\": \"$SID\", \"text\": \"smartwatch\"}"
```

### PUT /api/presearch/{session_id}/intent

Applies the user's edits to what we understood (category, use, budget, region, must-haves, exclusions).

- **Body:** `{"changes": {"<Intent field>": <new value>, ...}}`, for example `{"changes": {"use_case": "gaming"}}`
  or `{"changes": {"budget": {"max": {"amount": 60000, "currency": "INR"}}}}`.
- **Response 200:** the updated `Intent`.
- **Side effects:** any edit restarts the interview; changing the category or use also clears the criteria
  (call `criteria` again).
- **Errors:** 404; 409 (no intent yet); 422 `That change doesn't work: <field> - <reason>.` (the old intent is
  kept).

```bash
curl -s -X PUT http://localhost:8000/api/presearch/$SID/intent \
  -H "Content-Type: application/json" \
  -d '{"changes": {"use_case": "coding and light gaming"}}'
```

### POST /api/presearch/{session_id}/criteria

Works out the criteria to compare for the product and its use (US-03), each with a one-line definition. Calling
it again clears any interview.

- **Body:** none.
- **Response 200:** `CriteriaSet` (`source` is `generated`, `cached` or `fallback`).
- **Errors:** 404; 409 `Tell us what kind of product first.` (the intent is not ready for research).

```bash
curl -s -X POST http://localhost:8000/api/presearch/$SID/criteria
```

### POST /api/presearch/{session_id}/interview/start

Asks the first question. Calling it again restarts the interview.

- **Body:** none.
- **Response 200:** `InterviewStep` with `kind: "question"`. Show `message`; show `question.quick_replies`
  (`Low`, `Medium`, `High`, `Skip`) as buttons and allow free text; `question.why_asking` explains the question.
- **Errors:** 404; 409 (get the criteria first).

```bash
curl -s -X POST http://localhost:8000/api/presearch/$SID/interview/start
```

### POST /api/presearch/{session_id}/interview/reply

Sends the user's reply to the current question.

- **Body:** `{"reply_text": "<1 to 500 characters>"}`. Understood: the buttons `Low` (2), `Medium` (5), `High` (8),
  `Skip`, and a whole number from 0 to 10 (also `7/10`).
- **Response 200:** `InterviewStep`, by `kind`:
  - `question`: the next question.
  - `rephrase`: the reply was unclear; show `message` ("Please pick one of the buttons, or type a number from 0
    to 10.") with the same question's buttons. After two unclear replies the question is skipped.
  - `finished`: no questions left. `rubric` holds a weight for every criterion; send it in a `SearchRequest`.
- **Errors:** 404; 409 (interview not started, or already finished); 422 (empty or too long `reply_text`).

```bash
curl -s -X POST http://localhost:8000/api/presearch/$SID/interview/reply \
  -H "Content-Type: application/json" \
  -d '{"reply_text": "High"}'
```

### POST /api/presearch/{session_id}/interview/back

Asks an earlier question again so the user can change the answer. The next reply replaces the old answer, and
the later questions update (a question skipped because of an earlier "Low" can come back). Works after the
interview has finished too.

- **Body:** `{"question_id": "<the id of an answered question, e.g. q_battery_life>"}` (from `question.id`,
  or from `state.history` in any step).
- **Response 200:** `InterviewStep` with `kind: "question"` for that question.
- **Errors:** 404; 409 (interview not started, or that question has not been answered yet).

```bash
curl -s -X POST http://localhost:8000/api/presearch/$SID/interview/back \
  -H "Content-Type: application/json" \
  -d '{"question_id": "q_processor_speed"}'
```

### GET /api/presearch/{session_id}

Everything about the session, for a page reload: `request`, `unclear_attempts`, `intent`, `criteria`,
`interview` (the `InterviewState`) and `interview_step` (the latest step: redraw the current question, or the
finished rubric). Fields not reached yet are `null`.

- **Errors:** 404.

```bash
curl -s http://localhost:8000/api/presearch/$SID
```

## A whole interview in one go (Git Bash)

```bash
BASE=http://localhost:8000/api/presearch
SID=$(curl -s -X POST $BASE/intake -H "Content-Type: application/json" \
  -d '{"text": "best laptop for coding under 70k"}' | python -c "import json,sys; print(json.load(sys.stdin)['session_id'])")
curl -s -X POST $BASE/$SID/criteria > /dev/null
curl -s -X POST $BASE/$SID/interview/start
for answer in High Medium 7 Skip Low High Medium Medium; do
  curl -s -X POST $BASE/$SID/interview/reply -H "Content-Type: application/json" \
    -d "{\"reply_text\": \"$answer\"}"; echo
done
```
