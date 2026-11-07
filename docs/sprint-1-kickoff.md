# Sprint 1 kickoff

Sprint 1: 21 Sep – 4 Oct 2026 · 36 points · Scrum Master: Jhanvi Barot · Product Owner: Om Pandya

> **Sprint goal:** a request typed in the browser or the terminal runs through every stage and returns a
> ranked list, and the app clearly understands what the shopper wants.

## Before the planning meeting (Om, about 1 hour)

- [ ] Confirm the real submission deadline, and shift sprint dates if it is before 13 Dec
- [ ] Two-minute check with the TA that the extended requirements and new sprint table are acceptable
- [ ] Create the GitHub repo; add all 10 members; protect `main` (1 review, CI must pass)
- [ ] Commit this starter pack as the first commit (see README, "Who commits what")
- [ ] Create the Slack channels: `#general`, `#standup`, `#dev`, `#elicitation`, `#ceremonies`
- [ ] Run `scripts/create_github_issues.py` to create the 51 issues, labels and sprint milestones
- [ ] Create a GitHub Project board with columns Backlog, Ready, In Progress, In Review, Done; add all issues
- [ ] Share the plan doc and ask everyone to read §1, §6, §12 and §13 before the meeting

## Planning meeting agenda (90 minutes)

| Time | Item | Output |
|---|---|---|
| 0–10 | Sprint goal and the walking-skeleton idea (plan §6) | Everyone can say the goal in one sentence |
| 10–40 | **Contract review** (below) | Contracts accepted, or changes agreed and assigned |
| 40–60 | Walk through the 7 Sprint 1 stories; confirm or swap owners | Every task has an owner |
| 60–75 | Planning poker on any story the team disagrees with | Final points; commit to the sprint |
| 75–85 | Outside-points work: elicitation, API sign-up, poster, tests | Owners and dates |
| 85–90 | Standup time, review date (Sat 3 Oct), drill for the review | Posted in `#ceremonies` |

## Contract review (the most important 30 minutes)

Open `backend/app/contracts/` and `shared/contracts/examples/search_result.json`. For each stage owner:

- [ ] Does my stage's **input** contain everything I need?
- [ ] Does my stage's **output** contain everything the next stage and the UI need?
- [ ] Does the example data look like what my stage will really produce?
- [ ] Is my stage's time limit realistic (`STAGE_TIME_LIMITS_S`)?
- [ ] What does my **fallback** return?

Frontend owners also check that `search_result.json` has every state the results page must show.

Agree changes in the meeting. The US-52 owners apply them in one pull request by the end of day 2, then
the contracts are frozen for the sprint. After that, a change needs a message in `#dev` and a team
agreement.

## First week

| Day | Backend and pipeline | ML | Frontend | Quality and evidence |
|---|---|---|---|---|
| 1 | Contract changes from review; repo, CI green | Pick matching datasets; set up Colab | Next.js app shell | Survey live; test scaffolding |
| 2 | US-52 pipeline runner and stub stages | US-03 criteria generator | Request box on the mock data | Record first API responses |
| 3 | US-26 LLM wrapper (first provider end to end) | US-03 fallback set | Plain results page from `search_result.json` | Interviews start |
| 4–5 | US-02 detection; wrapper fallback chain | US-05 interview logic | Interview chat window | Observation, expert interview |

By day 5, `python -m app.cli "best laptop for coding under 70k"` should print a ranked list,
even if every stage is still a stub. From then on, every story replaces a stub with a real stage.

## Review on Sat 3 Oct: what we must show live

- [ ] CLI and browser both run a request through every stage and show a ranked list with sources
- [ ] Every stage has a contract test; forcing a stage to fail triggers its fallback
- [ ] Detection shows category, use, budget, region and rules; "watch" asks a question; with every LLM off, keywords still detect category and budget
- [ ] Criteria with definitions for three categories, including one never seen before; the fallback set appears when generation fails
- [ ] The interview chat runs at least 5 questions and stores the answers
- [ ] **Drill:** switch off one LLM provider key live; the search still finishes
- [ ] CI runs lint, types and tests on every PR; no secrets in the repo
- [ ] Every one of the 10 members has at least one merged PR under their own name
- [ ] Survey (30+), interviews, observation, expert interview and API limits written up
- [ ] Poster printed
