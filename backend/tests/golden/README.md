# Golden tests for broken output

A provider changing its output format should fail the build, not corrupt a ranking. So every parser has a folder of
malformed outputs here, and each one must give the canonical shape or a clean fallback, and never crash (US-48).

`pytest tests/golden` runs them. They use the real stage and the real LLM wrapper with a fake provider, and make no live
calls.

## Add a case

A case is two files in `<parser>/cases/`, with the same name:

- `<name>.raw`: the exact output. It is read as bytes, so a byte-order mark or a CRLF stays in it.
- `<name>.expected.json`: what must come of it.

```json
{"kind": "fenced", "given": {"request": "laptop under 70k"}, "outcome": "canonical", "object": {"raw_query": "..."}}
```

```json
{"kind": "trailing_comma", "outcome": "fallback", "fallback": "keyword_rules"}
```

- **`kind`:** `fenced`, `missing_keys`, `wrong_types`, `out_of_range`, `trailing_comma`, `cut_off`, `prose`,
  `extra_fields`, `embedded_instructions`, `empty`, `huge`, `other_schema`, `bom` or `specific`. Every parser of model
  output needs at least one of each, except `specific`.
- **`given`:** optional. What the stage was given, if it isn't the parser's default in `registry.py`.
- **`object`:** the whole result as `model_dump(mode="json", exclude_defaults=True)`, compared exactly. For extraction,
  `id`, `source_id`, `source_url`, `retrieved_at` and `extracted_at` are left out, as code sets them.
- **`fallback`:** one of the parser's named fallbacks.

| Parser | Fallback | Meaning |
|---|---|---|
| `intake` | `keyword_rules` | The same Intent the keyword rules give for this request |
| `criteria` | `family_set` | The category family's fixed criteria |
| `extract` | `source_dropped` | No claims or mentions, and the coverage note says a source couldn't be read |
| `research_reddit` | `items_skipped` | Nothing kept, and no error |

**Write the expectation from what should happen, before you run it.** If a case fails because the parser is wrong, fix
the parser. Never change a case to match wrong output.

## Add a parser

A new module named as a parser (`parsing.py`, `parser.py`, `parse.py`), or a new `llm.complete(...)` call, or a new schema
on an old task, fails `test_registry.py` until it has an entry in `PARSERS` in `registry.py` and at least 15 cases
covering every kind, one of them a 100 KB output. So:

1. Add its `Parser` to `PARSERS`: its folder, module, a `run` that calls it the way its stage does, its named fallbacks,
   and the calls whose answers it reads, as `"TASK: Schema"`.
2. Add `<parser>/cases/`, then its cases.

`model_output=False` is for a parser that doesn't read a model's answer, such as the Reddit archives' JSON. It still needs
15 cases, and is only excused from the kinds that can't apply to it.

## Which parsers don't exist yet

These have no model output to parse today. Adding one makes `test_registry.py` ask for its cases.

- **Interview reply:** `app/interview/answers.py` reads the shopper's text by table, with no LLM (until US-39).
- **Rubric:** `app/interview/rubric_builder.py` builds it in code from the answers.
- **Enrichment:** `app/enrich/stage.py` is a placeholder (US-41).
- **Explanation:** `app/explain/stage.py` is a placeholder (US-20).

The shared layer under every parser, `app/llm/output.py`, has its own table in `llm/test_output_parsing.py`.
