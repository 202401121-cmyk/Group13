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
- **Types:** `IntakeResponse`, `Intent`, `CriteriaSet`, `ReplyRequest`, `InterviewStep`, `PresearchSession` and the
  rest are the contract models. Their TypeScript types come from `shared/contracts/schema/*.json` (see
  `frontend/CLAUDE.md`); never redefine them by hand.
- **Sessions:** the first `intake` call returns a `session_id` (like `sess_3f9a…`). Send it with every later
  call. A session not used for 2 hours expires; the server keeps at most 1,000 at a time.
- **Errors** are always `{"detail": "<one plain sentence>"}`, never a stack trace. Show `detail` to the user.

| Status | Meaning |
|---|---|
| 200 | It worked. |
| 404 | `This session has expired. Please start a new search.` Start again with a new `intake`. |
| 409 | A step was called out of order (the message says which step comes first). |
| 422 | The body or an edit is not valid (the message names the field). |

- **CORS:** only the origins in the `FRONTEND_ORIGINS` environment variable (comma-separated, default
  `http://localhost:3000`) may call the API from a browser. `*` is never allowed. The older `FRONTEND_ORIGIN` (one
  origin) still works when `FRONTEND_ORIGINS` is not set.
- **No LLM key?** Everything still works: intake uses its keyword rules and criteria use fixed sets.

## The flow

```
POST /api/presearch/intake               {"text": "best laptop for coding under 70k"}
  └─ ready_for_research false? show intent.clarification_question and its options (or, if
     intent.show_example_requests, a few example requests), then POST /intake again with the reply
     and the same session_id
POST /api/presearch/{session_id}/criteria
POST /api/presearch/{session_id}/interview/start            → first question
POST /api/presearch/{session_id}/interview/reply  (repeat)  → next question, a rephrase, or "finished" + rubric
POST /api/presearch/{session_id}/interview/back   (any time) → ask an earlier question again
```

## Routes

### GET /api/health

Checks the server is up, and whether the database can be reached (US-01).

- **Response 200:** `{"status": "ok", "schema_version": "1.0.0", "database": "ok"}`
- `database` is `"ok"`, `"off"` (no `DATABASE_URL`: nothing is saved, everything else works) or `"down"` (set up
  but not reachable right now; searches still finish). Nothing else about the database is shown.

```bash
curl -s http://localhost:8000/api/health
```

### POST /api/presearch/intake

Reads a new request, or the reply to our clarification question.

- **Body:** `{"session_id": "<optional>", "text": "<up to 1,000 characters>"}`. Spaces around `text` are trimmed.
  - No `session_id`: a new search with a new session.
  - With the `session_id` of a session whose intent still needs clarification: `text` is the reply to our
    question (for example "smartwatch" after "Which kind of watch do you mean?"). If the reply is still
    unclear, the intent comes back with `show_example_requests: true` and no category: show a few example
    requests. A category is never guessed.
  - With the `session_id` of a session that is already clear: a fresh request in the same session (the
    criteria and interview are cleared).
  - Blank text (empty, or only spaces, tabs or newlines) is not an error. It gets a follow-up question ("What kind of
    product are you looking for?" with its options), or example requests the second time. Nothing is saved, and a
    request already in the session stays as it is. With no request yet, the next `text` in that session is the
    request. `intent.raw_query` in this one answer is `(nothing typed)`.
- **Response 200:** `IntakeResponse`: `{"session_id": "sess_…", "intent": Intent, "ready_for_research": true}`
  - `ready_for_research: false` → show `intent.clarification_question` with `intent.clarification_options` as
    buttons, or example requests when `intent.show_example_requests` is true.
- **Errors:** 404 (unknown or expired `session_id`), 422 (`text` missing, not a string, or over 1,000 characters).

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

Applies the user's edits to what we understood. Only the use, budget and region can change (US-02).

- **Body:** `{"changes": {"<Intent field>": <new value>, ...}}`, for example `{"changes": {"use_case": "gaming"}}`
  or `{"changes": {"budget": {"max": {"amount": 60000, "currency": "INR"}}}}`.
- **Response 200:** the updated `Intent`.
- **Side effects:** any accepted edit restarts the interview; changing the use also clears the criteria (call
  `criteria` again).
- **Errors:** 404; 409 (no intent yet); 422, see below. After a 422 the session is exactly as it was: nothing in
  the edit is applied, even a valid field beside a refused one.

| Intent field | Can an edit change it? | If not, the 422 `detail` |
|---|---|---|
| `use_case`, `budget`, `region` | Yes | A bad value: `That change doesn't work: <field> - <reason>.` |
| `category`, `category_label` | No. A new product always needs a new request, because the criteria and the interview depend on it. This stays after Sprint 3. | `To change the product, start a new request.` |
| `hard_rules`, `exclusions` | Not yet. US-09 and US-10 (Sprint 3) add this. | `Must-haves and leave-outs can't be changed here yet. Start a new request with the change instead.` |
| Every other Intent field | No. Only the app sets them. | `That change doesn't work: <field> - only the use, budget and region can be edited.` |

A field is refused only when its value really changes, so sending back a current value, or the whole `Intent` with
one field changed, is fine. A name that is not an Intent field gets `That change doesn't work: <field> - <reason>.`

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

- **Body:** `{"reply_text": "<1 to 500 characters>", "question_id": "<optional>"}`. Understood: the buttons `Low` (2),
  `Medium` (5), `High` (8), `Skip`, and a whole number from 0 to 10 (also `7/10`).
- **`question_id`** is the `question.id` of the question being answered, such as `q_battery_life`. Send it with every
  reply: then a network retry or a double click can never answer the next question by mistake.

  | `question_id` | What happens |
  |---|---|
  | Left out, or `null` | The reply goes to the current question. |
  | The current question | The reply goes to it. |
  | A question already answered, with the same answer | A safe repeat. Nothing changes, and you get 200 with the latest `InterviewStep`, even after the interview has finished. |
  | A question already answered, with a different answer | Nothing changes. 409: go back to that question first (`interview/back`). |
  | A question this session has not asked | Nothing changes. The same 409. |

  "The same answer" ignores capitals, extra spaces and a closing `.` or `!`. A question reopened with
  `interview/back` is the current question again. Two replies at the same moment are handled one after the other,
  so identical ones apply once.
- **Response 200:** `InterviewStep`, by `kind`:
  - `question`: the next question.
  - `rephrase`: the reply was unclear; show `message` ("Please pick one of the buttons, or type a number from 0
    to 10.") with the same question's buttons. After two unclear replies the question is skipped.
  - `finished`: no questions left. `rubric` holds a weight for every criterion; send it in a `SearchRequest`.
- **Errors:** 404; 409 (interview not started, already finished, or `question_id` as in the table above); 422 (empty
  or too long `reply_text`, or a `question_id` that is empty or over 64 characters).

```bash
curl -s -X POST http://localhost:8000/api/presearch/$SID/interview/reply \
  -H "Content-Type: application/json" \
  -d '{"reply_text": "High", "question_id": "q_processor_speed"}'
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

Everything about the session, as a `PresearchSession`, for a page reload: `request`, `unclear_attempts`, `intent`,
`criteria`, `interview` (the `InterviewState`) and `interview_step` (the latest step: redraw the current question,
or the finished rubric). Fields not reached yet are `null`; `request` is `""` until the shopper has typed one.

`request_saved` says whether the request reached the database (US-01): `"saved"`, `"off"` (no database is set
up) or `"failed"` (the database was down or slow; the search carries on). It is `null` until the first request.
This is about the request only; `SearchResult.saved` is about saving search results, which comes later.

- **Errors:** 404.

```bash
curl -s http://localhost:8000/api/presearch/$SID
```

## A whole interview in one go (Git Bash)

The number of questions changes from one interview to the next: it depends on the criteria and on the answers. So
this example keeps answering until the step's `kind` is `finished`, then shows the rubric. If the server sends an
error, the answer has no `kind`, so the loop stops and prints the error. It leaves `SID` and `RUBRIC` set, ready for
[`POST /api/search`](../frontend-api.md#post-apisearch).

```bash
BASE=http://localhost:8000/api/presearch
# Prints one field of the JSON it is given: text as it is, anything else as JSON, nothing if the field is missing.
field() {
  python -X utf8 -c "import json,sys; v=json.load(sys.stdin).get('$1',''); print(v if isinstance(v,str) else json.dumps(v))"
}

SID=$(curl -s -X POST $BASE/intake -H "Content-Type: application/json" \
  -d '{"text": "best laptop for coding under 70k"}' | field session_id)
curl -s -X POST $BASE/$SID/criteria > /dev/null
STEP=$(curl -s -X POST $BASE/$SID/interview/start)
KIND=$(echo "$STEP" | field kind)

ANSWERS=(High Medium 7 Skip Low)
TURN=0
while [ -n "$KIND" ] && [ "$KIND" != "finished" ]; do
  ANSWER=${ANSWERS[$((TURN % ${#ANSWERS[@]}))]}
  TURN=$((TURN + 1))
  echo "$(echo "$STEP" | field message) -> $ANSWER"
  STEP=$(curl -s -X POST $BASE/$SID/interview/reply -H "Content-Type: application/json" \
    -d "{\"reply_text\": \"$ANSWER\"}")
  KIND=$(echo "$STEP" | field kind)
done

if [ "$KIND" = "finished" ]; then
  echo "$STEP" | field message
  RUBRIC=$(echo "$STEP" | field rubric)
  echo "$RUBRIC" | python -m json.tool
else
  echo "Stopped early: $STEP"
fi
```
