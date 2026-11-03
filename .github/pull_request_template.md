## Story

**US-xx**: <story title>  <!-- if the story has a card on the Projects board, link it here -->

## What this changes

<!-- Two or three sentences. What does the user or the pipeline do differently now? -->

## How to check it

<!-- Commands or clicks a reviewer can use, e.g. `pytest tests/unit/research -q` or `python -m app.cli "..."` -->

## Hand-over report

<!-- Paste the hand-over report here: commits with author and committer, evidence per acceptance criterion,
     every check with its result, deviations, gaps with owners. -->

## Screenshots

<!-- Any screen: 390 px and 1440 px wide, light and dark. Delete this section if nothing visual changed. -->

## Definition of Done

- [ ] Every acceptance criterion in `backlog/stories.yaml` is met (list any that are not, and why)
- [ ] Unit tests and the stage's contract test are written and pass
- [ ] Golden tests added for any new LLM parser
- [ ] A fault test added for any new fallback
- [ ] No change to `app/contracts/` (or: the team agreed it, and `shared/contracts/` and the frontend types are regenerated in the same commit)
- [ ] None of the ten build rules in `CLAUDE.md` is broken
- [ ] No secret or API key in the code
- [ ] Every commit is mine (`git log --format='%an <%ae>' main..HEAD`)
- [ ] I can explain every line of this change

## Reviewer checklist

- [ ] Meets the acceptance criteria
- [ ] Tests are real (they would fail if the code were wrong)
- [ ] Follows the ten build rules
- [ ] Understandable without asking the author
