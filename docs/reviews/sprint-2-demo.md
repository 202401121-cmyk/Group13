# Sprint 2 review demo: run sheet

Fifteen minutes at most. Yashkumar (Sprint 2 Scrum Master) runs the meeting; one laptop drives the demo, with the
repository at the latest `main`. The sprint's goal was real research and evidence, with the test safety net.

**The day before:** check that `main` has the three close-out pull requests: Groq as the last resort for public pages
(`fix/US-46-public-last-resort`), the re-check (`chore/sprint-2-recheck`) and this run sheet (`docs/sprint-2-close`).
Then, on the demo laptop: `git pull`; in `backend/`, `pip install -e ".[dev]"` (it brings python-dotenv 1.2.4 or newer,
which reads `KEY= # note` in `.env` as empty); open the Supabase dashboard once, so the free project is not paused; and
run `python scripts/doctor.py`: it must say "Nothing failed". Last, less than 23 hours before the demo, run the demo
request once, `python -m app.cli "Laptop for coding under 80k" --claims --no-db` from `backend/`, so its search results
are still in the research cache (24 hours; Reddit's a little less) and the live search starts quickly.

## Running order

| Time | Who | What they show | What the audience should see |
|---|---|---|---|
| 0:00–1:00 | Yashkumar | The Sprint 2 goal: real research and evidence, with the test safety net. | |
| 1:00–2:00 | Pal | Start-up: `python scripts/doctor.py`, then `python scripts/dev.py`. | The doctor's table with no FAIL. Then the frontend and backend addresses, "database: ok", and how many LLM providers have a key. |
| 2:00–3:30 | Pal, Manu | **Rough answers (US-08):** type **"Laptop for coding under 80k"**, then answer the questions with the buttons **Low**, **Medium**, **High** and **Good enough**, and one exact number in the exact-value box. | Each answer is taken as a level or a number; the brief shows the weights. |
| 3:30–5:30 | Yashkumar, Jenish | **Research in parallel (US-12, US-27):** start research and watch the progress screen. | One row per stage, saying what it is doing, with the sources found so far by kind, then the results with their coverage note. A Stop button is there throughout. |
| 5:30–7:00 | Jhanvi | **Claims with exact quotes (US-14):** in a terminal, from `backend/`, `python -m app.cli "Laptop for coding under 80k" --claims --no-db`. | "Claims per source": each claim with its product, criterion, value or score, and the exact quote, under its source's link; then "LLM calls per provider". |
| 7:00–8:00 | Vaishvi | **Reddit quality (US-13)**, in the same output. | "Reddit threads (… kept, … dropped)" with each thread's score, and "Recommenders per product". |
| 8:00–9:00 | Pal | **Untrusted pages (US-28):** from `backend/`, `pytest tests/faults/test_hostile_pages.py -v`. | 33 passed: each of the 10 hostile pages gives the same result as its clean twin. |
| 9:00–10:00 | Palak, Harshkumar | **The safety net (US-48, US-32):** from `backend/`, `pytest tests/golden -q`; then, in the last pull request's CI run, the backend job's coverage gates step (the whole suite with coverage ran 191 s in the re-check, too long here). | Every golden case passes; "Coverage gates passed: 70% on core modules, 80% on app/decide/." |
| 10:00–11:30 | Palak | **Drill, Reddit off:** Git Bash `REDDIT_SOURCE=off python -m app.cli "Laptop for coding under 80k" --no-db`; PowerShell `$env:REDDIT_SOURCE="off"; python -m app.cli "Laptop for coding under 80k" --no-db`, then `Remove-Item Env:REDDIT_SOURCE` so Reddit is back on. | The results arrive, and the coverage note says "Reddit could not be read." |
| 11:30–12:30 | Jhanvi | **Drill, a provider rate-limited (US-46):** the CLI can't make a provider answer 429 without changing the app, so its fault test runs live: `pytest tests/faults/test_rate_limit_drill.py -v`. | 4 passed. One is the review check's drill: Gemini says 429, Mistral's minute is full, OpenRouter is slow, and Groq reads every page, with no message for the shopper. |
| 12:30–13:30 | Palak | **CI and the review check:** the green checks on the last pull request, and section 9 of [sprint-2-check.md](sprint-2-check.md). | All five jobs green, and the verdict: "Sprint 2 verified (accepted by the Product Owner, 10 Nov: F-1, F-2, F-7 to F-10)". |
| 13:30–14:15 | Om, Rushiraj | **The ML work:** from the root, `python ml/scripts/labelling_status.py` (ML-1), then `python ml/scripts/trust_baseline.py` (task #30). | "275 distinct pairs in 350 rows", with a four-class kappa of 0.933 between the two machine labellers on the shared batch; then the trust tables on the 5 conflicts entered (15 were planned): majority vote 0 of 5, tier order 3 of 5. |
| 14:15–15:00 | Yashkumar | What moves to Sprint 3, and what was accepted (below). | |

**Said plainly at 14:15.**

- **Moved to Sprint 3 by D-61:** the matcher's training data and notebook v0 (task #29); the evaluation on our own
  pairs (L2); our own pairs' labelling (L1's runbook); task #30's conflicts past the 5 entered; and testing filtering
  and ranking offline (US-32), which waits for US-17 and US-18.
- **Accepted by the Product Owner on 10 Nov, and not fixed:** the fast suite takes 64 s on Python 3.11 against its
  60 s limit (F-1); CI does not block a merge, because `main` is not protected (F-2); the pull requests were opened from
  Om's account (F-7, settled by D-05 as amended on 10 Nov); and pull request #50's description, two Sprint 1 branch
  names and seven commit messages from before Sprint 2 name an AI tool (F-8 to F-10; `main`'s history is not
  rewritten).

## If something fails during the demo

- **No network, or a provider is down:** run `python scripts/dev.py --mock` and walk the same screens with sample
  data. For the terminal, show the recorded runs in the review check's evidence folder:
  [recheck-l1-reddit-off.txt](sprint-2-check/recheck-l1-reddit-off.txt) for the claims and the Reddit-off drill, and
  [recheck-f6-after-fix.txt](sprint-2-check/recheck-f6-after-fix.txt) for the rate-limit drill.
- **The search takes long:** a whole search stops at 150 seconds and returns what it has, marked as partial. Say so,
  and go on with the next row.
- **The database is paused:** say so. The app carries on with `database: down`. Resume the project in the dashboard
  after the demo.
- **Port 8000 or 3000 is busy:** `python scripts/dev.py --backend-port 8001` or `--frontend-port 3001`, or close
  the old terminal.

Only made-up shopping requests are typed in the demo, never anything personal.

## Retrospective (ten minutes, after the demo)

Yashkumar runs it, and writes the answers in the team's notes. Bring these facts from the repository:

- **Pull requests:** 28 merged in Sprint 2 (#50 to #77), with commits from all ten members, and the three close-out
  branches after them. Every one was opened from Om's account (F-7).
- **The review check:** the first run on `b6ec081` found 14 failures (10 FAIL, 4 BLOCKED). The Fix Pack (#75 to
  #77) and the re-check left one, F-6, which Groq as the last resort for public pages fixed. Six findings were
  accepted.
- **Moved to Sprint 3:** the D-61 items above.
- **Open gaps:** the review pages that reach extraction without their review text, and the criteria a public page may
  be asked about (section 9's notes in [sprint-2-check.md](sprint-2-check.md)).

| Time | Item | Output |
|---|---|---|
| 0–1 | The facts above | Everyone starts from the same facts |
| 1–4 | What went well | The team's answers |
| 4–7 | What to change | The team's answers |
| 7–10 | Actions, each with one owner and a date | Each action written down with its owner |
