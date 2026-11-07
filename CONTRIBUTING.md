# Contributing to ShopSense

Everyone builds their own stories, in their own clone, under their own GitHub account.
Commit attribution is checked by the course, so **the person who did the work makes the commit**.

## One-time setup

```bash
git config --global user.name  "Your Full Name"        # the name on your GitHub account
git config --global user.email "you@example.com"       # an email verified on your GitHub account
git clone https://github.com/202401121-cmyk/Group13.git
cd Group13
cp .env.example .env                                    # one root .env; fill in only the keys your story needs

# backend
cd backend
python -m venv .venv && source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q                                               # should pass before you change anything
cd ..

# frontend
cd frontend && npm ci && npm test && cd ..

# ml (only if your story touches ml/)
pip install -e "backend[dev]" -r ml/requirements.txt
```

Check that `git config user.email` matches an email on your GitHub account; otherwise your commits won't count as yours.
Once W8 adds it, `docs/setup.md` covers the whole stack, the doctor script and troubleshooting.

Windows: use Git Bash or PowerShell. After cloning, run `git config core.autocrlf false`;
`.gitattributes` keeps line endings consistent.

Get the team's local kit from Om and run its installer from your clone's root. It holds the developer tool's
local settings and the git hooks that check your commits. It is shared privately and never committed.

## Building a story, step by step

Work is organised by user story (US-xx). The story's acceptance criteria in `backlog/stories.yaml` define the task.

1. **Pick** your story at sprint planning. Its owner is in the sprint plan.
2. **Branch** from a fresh `main`:
   ```bash
   git switch main && git pull
   git switch -c feature/US-12-research-queries
   ```
3. **Get the written prompt** for your story from the team's planning chat. Add your full name and the email on
   your GitHub account. The prompt carries the acceptance criteria, what already exists, the rules that apply, the
   checks and the hand-over format.
4. **Build in your own clone** from that prompt, with the team's local kit installed. Read the plan, correct it,
   then approve it.
5. **Read every change.** Tests are written with the code, and ruff, mypy, pytest and the other checks run. You
   must be able to explain every line in a viva.
6. **The task ends with one local commit under your name** and a hand-over report. Paste the hand-over into the
   planning chat for review, and apply any fixes to that same commit.
7. **Check the authorship**, take the newest `main`, and re-run the checks:
   ```bash
   git log --format='%h %an <%ae> | %cn <%ce>' main..HEAD   # every line must be you
   git fetch origin && git merge origin/main
   ```
8. **Review the commit, push the story branch and open the pull request**: title
   `US-xx: <story title>`, the hand-over pasted into the description, screenshots at 390 px and 1440 px (light and
   dark) for any screen. Ask the area owner to review.
9. **Merge** after green CI (Om merges). The reviewer should look first, but Om may merge without waiting for the
   approval. Delete the branch. Every other open branch now takes the new `main` and re-runs its checks before its
   next push.

## Rules

- One story per branch and pull request. Keep pull requests small; merge within one or two days.
- Pull `main` every day, and merge it into your branch if your branch is behind.
- Never push to `main` directly. Never commit `.env` or any key.
- Don't change a file in `app/contracts/` inside a story without the team's agreement. A contract change goes in
  one commit: the model, the contract export and the regenerated frontend types. Tell Yashkumar and Manu first.
- Pair work: both people genuinely work on it, one commits, and adds
  `Co-authored-by: Partner Name <partner@example.com>` as the last line of the commit message.
- Stuck for more than an hour? Post in `#dev` with what you tried.

## The ten build rules

Never break these. Code comments cite them by number.

1. Every LLM call goes through the one wrapper (`app/llm/`, `LLMClient.complete`). Never import a provider SDK
   anywhere else. The wrapper fits input to the model's context window, validates output, and falls back.
2. Code decides. The LLM never filters, scores or ranks products; `app/decide/` is plain Python.
3. Web, Reddit and video text is data, never instructions. Wrap it as data in prompts (`wrap_as_data()` once
   US-28 adds it); accept only schema JSON back.
4. Every claim keeps its source: URL, exact quote, retrieval time.
5. No product without a source. Products with no mention and no source are dropped before scoring.
6. Unknown is not bad. Never score a missing value as zero. Look it up, else peer-average it and mark "estimated".
7. Cache everything expensive (criteria per category, LLM answers by content hash, searches for 24 h).
8. Personal data never reaches a provider that trains on inputs. Every LLM call declares a data class (private:
   anything the shopper typed; reddit; public), and tasks in `PERSONAL_TASKS` are always private. Providers
   marked `trains_on_inputs: true` in `config/models.yaml` (Gemini free tier, OpenRouter free models, Mistral's
   free plan) only ever get public text. A test proves it for every new task.
9. Every stage has a time limit and a fallback. No stage may hang or crash the search.
10. The pipeline never needs the database to finish. Keep state in memory; save at the end.

## Reviewing a pull request

Area owners: backend Pal or Jenish; database Vaishvi; CI Palak; ML Om; frontend Yashkumar or Manu. Check, in order:

1. Does it meet every acceptance criterion in `backlog/stories.yaml`?
2. Are there tests, including golden tests for any LLM parsing and a fault test for any fallback?
3. Does it break any of the ten build rules above?
4. Is every commit by the pull request's author (`git log --format='%an <%ae>'`)?
5. Could you explain this code to the instructor?

Approve only if all five are yes. Leave specific comments; "looks good" is not a review.

## Definition of Done

- [ ] Every acceptance criterion met and demonstrated
- [ ] Unit tests and the stage's contract test written and passing
- [ ] Golden tests for any new LLM parser; a fault test for any new fallback
- [ ] A named reviewer asked to review (Om may merge without waiting for the approval)
- [ ] Merged to `main` with every CI check green (and the quality gate, once it exists)
- [ ] No secret or API key in the code
- [ ] Docs or docstrings updated

## Daily standup (Slack `#standup`)

Reply to the day's thread before 10 pm:

```
Yesterday: US-12 query builder done, PR open
Today: US-12 review fixes, start Reddit fetch
Blockers: none
```
