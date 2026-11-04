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

## Building a story, step by step

Work is organised by user story (US-xx). The story's acceptance criteria in `backlog/stories.yaml` define the task.

1. **Pick** your story at sprint planning. Its owner is in the sprint plan.
2. **Branch** from a fresh `main`:
   ```bash
   git switch main && git pull
   git switch -c feature/US-12-research-queries
   ```
3. **Get the prompt** for your story from the team's planning chat. Give it your full name and the email on your
   GitHub account. The prompt carries the acceptance criteria, what already exists, the rules that apply, the
   checks and the hand-over format.
4. **Start Claude Code in the repository root** (it reads `CLAUDE.md` and `.claude/settings.json` from there) and
   paste the prompt. It starts in plan mode: read the plan, correct it, then approve it.
5. **Let it build, and read every change.** It writes tests with the code and runs ruff, mypy, pytest and the
   other checks. You must be able to explain every line in a viva.
6. **Claude Code commits under your name** and ends with a hand-over report. Paste the hand-over into the planning
   chat for review and apply any fixes it lists.
7. **Check the authorship**, take the newest `main`, and re-run the checks:
   ```bash
   git log --format='%h %an <%ae> | %cn <%ce>' main..HEAD   # every line must be you
   git fetch origin && git merge origin/main
   ```
8. **Push the story branch** (yourself, or ask Claude Code to push it) and **open the pull request**: title
   `US-xx: <story title>`, the hand-over pasted into the description, screenshots at 390 px and 1440 px (light and
   dark) for any screen. Ask the area owner to review.
9. **Merge** after approval and green CI (Om merges). Delete the branch. Every other open branch now takes the new
   `main` and re-runs its checks before its next push.

## Rules

- One story per branch and pull request. Keep pull requests small; merge within one or two days.
- Pull `main` every day, and merge it into your branch if your branch is behind.
- Never push to `main` directly. Never commit `.env` or any key.
- Don't change a file in `app/contracts/` inside a story without the team's agreement. A contract change goes in
  one commit: the model, the contract export and the regenerated frontend types. Tell Yashkumar and Manu first.
- Pair work: both people genuinely work on it, one commits, and adds
  `Co-authored-by: Partner Name <partner@example.com>` as the last line of the commit message.
- Stuck for more than an hour? Post in `#dev` with what you tried.

## Reviewing a pull request

Area owners: backend Pal or Jenish; database Vaishvi; CI Palak; ML Om; frontend Yashkumar or Manu. Check, in order:

1. Does it meet every acceptance criterion in `backlog/stories.yaml`?
2. Are there tests, including golden tests for any LLM parsing and a fault test for any fallback?
3. Does it break any of the ten build rules in `CLAUDE.md`?
4. Is every commit by the pull request's author (`git log --format='%an <%ae>'`)?
5. Could you explain this code to the instructor?

Approve only if all five are yes. Leave specific comments; "looks good" is not a review.

## Definition of Done

- [ ] Every acceptance criterion met and demonstrated
- [ ] Unit tests and the stage's contract test written and passing
- [ ] Golden tests for any new LLM parser; a fault test for any new fallback
- [ ] Reviewed and approved by another member
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
