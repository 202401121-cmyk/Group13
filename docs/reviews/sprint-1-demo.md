# Sprint 1 review demo: run sheet

Fifteen minutes at most. Jhanvi (Sprint 1 Scrum Master) runs the meeting; one laptop drives the demo, with the
repository at the latest `main`.

**The day before:** open the Supabase dashboard once, so the free project is not paused. Then, on the demo laptop,
run `python scripts/doctor.py`: it must say "Nothing failed", and "Database: reachable, Postgres 17".

## Running order

| Time | Who | What they show | What the audience should see |
|---|---|---|---|
| 0:00–1:00 | Jhanvi | The Sprint 1 goal: a request runs end to end, and the app understands the shopper. | |
| 1:00–2:30 | Pal | Start-up: `python scripts/doctor.py`, then `python scripts/dev.py`. | The doctor's table with no FAIL. Then "Frontend http://localhost:3000 (live mode)", "Backend … (database: ok)", and "LLM providers with a key: 5 of 5". |
| 2:30–4:30 | Jhanvi | Type **"Gaming laptop under 80k"**. | "What we understood": Laptop, gaming, up to ₹80,000, India. |
| 4:30–5:30 | Jhanvi | Type **"watch"**. | A follow-up asks which kind of watch, with buttons. |
| 5:30–7:00 | Jhanvi, Jenish | Type **"telescope for stargazing under 30000"** (a product the app has never seen), then open the criteria. | Telescope, stargazing, ₹30,000; criteria, each with a one-line definition. |
| 7:00–9:00 | Manu, Yashkumar | Answer the interview with the quick replies, review the brief, then start research. | One question at a time, the brief, then a ranked list with a score, why it ranks there, and a store link with its source. |
| 9:00–10:00 | Vaishvi | `python scripts/check_request_saved.py <session ID>`; the ID is in the address bar (`/s/sess_…/`). | "saved". |
| 10:00–11:30 | Palak | **Drill, one key off:** stop with Ctrl+C, then start with Groq off (Git Bash: `GROQ_API_KEY= python scripts/dev.py`; PowerShell: `$env:GROQ_API_KEY=" "; python scripts/dev.py`), and type a laptop request again. | "LLM providers with a key: 4 of 5"; the flow still reaches results. |
| 11:30–12:30 | Palak | **Drill, database down:** start with `DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:9/postgres" python scripts/dev.py`, then open `http://localhost:8000/api/health`. | Health says `"database":"down"`, and a search still finishes. |
| 12:30–13:30 | Palak | CI: the green checks on the last pull request, and the review check ([sprint-1-check.md](sprint-1-check.md)). | All five jobs green, and the rows still open, with their owners. |
| 13:30–14:30 | Om, Rushiraj | The ML workspace: the labelling checks and the matching baseline (`ml/MODELS.md`). | The baseline numbers and the Sprint 2 plan for our own labelled pairs. |
| 14:30–15:00 | Harshkumar | Elicitation: what the survey and interviews found, if the write-up is ready. | |

## If something fails during the demo

- **No network, or a provider is down:** run `python scripts/dev.py --mock` and walk the same screens with sample
  data. For the terminal, show the recorded CLI runs in
  [sprint-1/cli-detection-and-criteria.txt](sprint-1/cli-detection-and-criteria.txt) and
  [sprint-1/cli-drills.txt](sprint-1/cli-drills.txt).
- **The database is paused:** say so. The app carries on with `database: down`, which is itself the drill. Resume the
  project in the dashboard after the demo.
- **Port 8000 or 3000 is busy:** `python scripts/dev.py --frontend-port 3001`, or close the old terminal.

Only made-up shopping requests are typed in the demo, never anything personal.
