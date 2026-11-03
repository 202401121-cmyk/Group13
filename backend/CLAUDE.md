# Backend — extra instructions for Claude Code

## Folder per stage

Every stage lives in `app/<stage>/` with the same layout:

```
app/research/
  __init__.py
  stage.py        class ResearchStage: name, run(inp, ctx), fallback(inp, ctx, error)
  parsing.py      lenient models for raw LLM/API output + to_contract() converters (if the stage parses anything)
  prompts.py      prompt text as constants (if the stage calls the LLM)
tests/unit/research/  tests/golden/research/  tests/faults/research/
```

A stage's `run` takes its `...Input` contract and returns its `...Result` contract. It receives its
dependencies (LLM client, HTTP client, cache) through the constructor, so tests can pass fakes.

```python
class ResearchStage:
    name = StageName.RESEARCH

    def __init__(self, llm: LLMClient, http: httpx.Client, cache: Cache) -> None: ...

    def run(self, inp: ResearchInput, ctx: StageContext) -> ResearchResult: ...

    def fallback(self, inp: ResearchInput, ctx: StageContext, error: Exception) -> ResearchResult: ...
```

## Replace, never rewrite

A better version of a stage (for example the fine-tuned matcher) is a new class with the same input and
output. The old class becomes what its `fallback` calls. Never delete a baseline.

## Calling the LLM

```python
result = llm.complete(
    LLMTask.EXTRACT_CLAIMS,
    SYSTEM_PROMPT,
    wrap_as_data(source.text),
    RawExtraction,
    data_class=DataClass.REDDIT if source.type is SourceType.REDDIT else DataClass.PUBLIC,
    difficulty=Difficulty.LONG,
)
```

- Every call declares `data_class` (what the prompt carries: `PRIVATE` for the shopper's words, `REDDIT`, or
  `PUBLIC`) and `difficulty` (`SHORT`, `MEDIUM`, `LONG`). Pick the most private class of anything in the prompt.
  The routes table in `config/models.yaml` turns the pair into a provider chain (US-46; rules in
  `docs/api-setup.md`). Never send private or Reddit text to a provider that trains on inputs (rule 8 in the
  root CLAUDE.md).
- `RawExtraction` is the lenient parsing model in `parsing.py`, not a contract.
- Put untrusted text inside `wrap_as_data()` (added by US-28) so it is marked as data.
- Catch `LLMUnavailable` and use the stage's fallback. Never retry in a loop yourself; the wrapper does it.

## Testing a stage

- Use `app.contracts.examples` for realistic inputs.
- Use a fake `LLMClient` that returns canned parsing models; never a real provider.
- Recorded HTTP responses go in `tests/fixtures/<stage>/`.
- Each stage needs: unit tests, a contract test (input in → valid output out), and a fault test for
  its fallback (make `run` raise and check the fallback's output is valid and the report says FALLBACK).
