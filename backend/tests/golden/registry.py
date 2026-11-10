"""Every parser of output we don't control, how its stage runs it, and what its fallbacks mean (US-48).

A case is two files in <parser>/cases/: <name>.raw holds the exact output, and <name>.expected.json says what must
come of it. test_golden_cases.py runs every case, and test_registry.py fails if a parsing module has no entry here or
too few cases. README.md explains how to add both.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.contracts import (
    CriteriaInput,
    CriteriaSet,
    ExtractInput,
    ExtractionResult,
    IntakeInput,
    Intent,
    Provider,
    Source,
    SourceComment,
    StageContext,
)
from app.criteria import CriteriaStage
from app.criteria.fallback import fallback_set
from app.extract import GuardedExtractStage
from app.intake import IntakeStage
from app.intake.rules import detect_by_rules
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.research.reddit.parsing import RawComment, RawPost, parse_items
from tests.fakes.llm import FakeAdapter
from tests.fakes.reddit import NOW
from tests.fakes.research import review_source, thread
from tests.golden.extract.test_extraction_parsing import CLAIMS_CODE_REFUSES, REPLIES_THE_SCHEMA_REFUSES

GOLDEN = Path(__file__).resolve().parent
APP = GOLDEN.parents[1] / "app"
MIN_CASES = 15

# Criterion 2's kinds, and the prompt's edge cases. Every parser of model output must have at least one of each.
REQUIRED_KINDS = frozenset(
    {
        "fenced",
        "missing_keys",
        "wrong_types",
        "out_of_range",
        "trailing_comma",
        "cut_off",
        "prose",
        "extra_fields",
        "embedded_instructions",
        "empty",
        "huge",
        "other_schema",
        "bom",
    }
)
KINDS = REQUIRED_KINDS | {"specific"}

SEARCH_ID = "srch_golden"
REQUEST = "gaming laptop under 80k with at least 16 GB RAM, no HP"
CRITERIA_INPUT = {"category": "laptop", "category_label": "Laptop", "use_case": "gaming"}
SOURCE_TEXT = "Nova Book 14 scored 8 out of 10 for battery_life in our tests."
SOURCE_URL = "https://www.reviews.example/a"
# What a claim holds besides what the model said: hashes and clock values, or copies of the source's own fields.
CLAIM_FIELDS_SET_BY_CODE = {"id", "source_id", "source_url", "retrieved_at", "extracted_at"}


@dataclass(frozen=True)
class Case:
    parser: str
    name: str
    raw: str
    expected: Mapping[str, Any]


@dataclass(frozen=True)
class Ran:
    """What came of one case."""

    result: Any
    view: Any  # what the case's "object" is compared with
    attempts: tuple[str, ...] = ()  # each LLM attempt's outcome
    note: str | None = None


Given = Mapping[str, Any]


@dataclass(frozen=True)
class Parser:
    name: str  # its folder under tests/golden/
    module: str
    run: Callable[[str, Given], Ran]
    fallbacks: Mapping[str, Callable[[Ran, Given], bool]]
    calls: frozenset[str] = frozenset()  # the LLM calls it reads the answers of, as "TASK: Schema"
    model_output: bool = True  # False: not a model's answer, so fences and prose can't apply
    reused: Callable[[], list[Case]] = field(default=list)  # cases that live in a test of their own


def _client(reply: str) -> tuple[WrapperLLMClient, InMemoryCallLog]:
    """The real wrapper, with every provider giving this reply."""
    log = InMemoryCallLog()
    adapters = {provider: FakeAdapter(provider, replies=(reply,)) for provider in Provider}
    return WrapperLLMClient(load_llm_config(), dict(adapters), log, limiters={}), log


def _attempts(log: InMemoryCallLog) -> tuple[str, ...]:
    return tuple(record.outcome for record in log.records)


# --- intent ----------------------------------------------------------------------------------------------------------


def _request(given: Given) -> str:
    return str(given.get("request", REQUEST))


def _run_intake(raw: str, given: Given) -> Ran:
    client, log = _client(raw)
    intent: Intent = IntakeStage(client).run(IntakeInput(raw_query=_request(given)), StageContext(search_id=SEARCH_ID))
    return Ran(intent, intent.model_dump(mode="json", exclude_defaults=True), _attempts(log))


def _is_keyword_rules(ran: Ran, given: Given) -> bool:
    return bool(ran.result == detect_by_rules(_request(given)))


# --- criteria --------------------------------------------------------------------------------------------------------


def _criteria_input(given: Given) -> CriteriaInput:
    return CriteriaInput.model_validate({**CRITERIA_INPUT, **given})


def _run_criteria(raw: str, given: Given) -> Ran:
    client, log = _client(raw)
    result: CriteriaSet = CriteriaStage(client).run(_criteria_input(given), StageContext(search_id=SEARCH_ID))
    return Ran(result, result.model_dump(mode="json", exclude_defaults=True), _attempts(log))


def _is_family_set(ran: Ran, given: Given) -> bool:
    return bool(ran.result == fallback_set(_criteria_input(given)))


# --- extraction ------------------------------------------------------------------------------------------------------


def _source(given: Given) -> Source:
    """A review page by default, or a Reddit thread with these comments."""
    text = str(given.get("text", SOURCE_TEXT))
    comments = given.get("comments")
    if comments is None:
        return review_source(SOURCE_URL, text=text)
    read = tuple(SourceComment(id=f"c{i}", depth=1, upvotes=1, text=c) for i, c in enumerate(comments))
    return Source.model_validate(
        {**thread("abc123", title=str(given.get("title", "A made-up title")), text=text).model_dump(), "comments": read}
    )


def _run_extract(raw: str, given: Given) -> Ran:
    client, log = _client(raw)
    source = _source(given)
    category = str(given.get("category", "laptop"))
    inp = ExtractInput(source=source, criterion_keys=["battery_life"], category_label=category)
    stage = GuardedExtractStage(client, now=lambda: NOW)
    result: ExtractionResult = stage.run(inp, StageContext(search_id=SEARCH_ID))
    for claim in result.claims:  # code sets these from the source, whatever the model said (build rule 4)
        assert (claim.source_id, claim.source_url, claim.retrieved_at) == (source.id, source.url, source.fetched_at)
        assert claim.extracted_at == NOW
    view: dict[str, Any] = {
        "mentions": [m.model_dump(mode="json", exclude_defaults=True) for m in result.mentions],
        "claims": [
            c.model_dump(mode="json", exclude_defaults=True, exclude=CLAIM_FIELDS_SET_BY_CODE) for c in result.claims
        ],
    }
    note = stage.coverage_note(SEARCH_ID)
    if note is not None:  # so an empty answer that code cleaned can't be mistaken for a source that was never read
        view["note"] = note
    return Ran(result, view, _attempts(log), note)


def _is_source_dropped(ran: Ran, given: Given) -> bool:
    empty = ran.result.claims == [] and ran.result.mentions == []
    return empty and "could not be read" in (ran.note or "")


def _param_cases(params: Iterable[Any], expected: Mapping[str, Any]) -> Iterator[Case]:
    for param in params:
        kind = REUSED_KINDS.get(param.id, "specific")
        yield Case("extract", f"p10-{param.id}", param.values[0], {"kind": kind, **expected})


# Which of the cases that live in test_extraction_parsing.py stand for a required kind.
REUSED_KINDS = {
    "missing-claims": "missing_keys",
    "score-as-text": "wrong_types",
    "score-out-of-range": "out_of_range",
    "trailing-comma": "trailing_comma",
    "cut-off": "cut_off",
    "only-prose": "prose",
    "extra-field": "extra_fields",
    "injected-instruction-as-a-quote": "embedded_instructions",
    "empty": "empty",
}


def _extract_cases_from_p10() -> list[Case]:
    dropped = {"outcome": "fallback", "fallback": "source_dropped"}
    nothing_kept = {"outcome": "canonical", "object": {"mentions": [], "claims": []}}
    return [*_param_cases(REPLIES_THE_SCHEMA_REFUSES, dropped), *_param_cases(CLAIMS_CODE_REFUSES, nothing_kept)]


# --- Reddit archives -------------------------------------------------------------------------------------------------


def _run_reddit(raw: str, given: Given) -> Ran:
    model = RawComment if given.get("model") == "comment" else RawPost
    items: list[Any] = parse_items(json.loads(raw), model)
    return Ran(items, {"items": [item.model_dump(mode="json", exclude_defaults=True) for item in items]})


def _is_items_skipped(ran: Ran, given: Given) -> bool:
    return ran.result == []


# --- the registry ----------------------------------------------------------------------------------------------------

PARSERS: dict[str, Parser] = {
    parser.name: parser
    for parser in (
        Parser(
            "intake",
            "app.intake.parsing",
            _run_intake,
            {"keyword_rules": _is_keyword_rules},
            frozenset({"DETECT_INTENT: RawIntent"}),
        ),
        Parser(
            "criteria",
            "app.criteria.parsing",
            _run_criteria,
            {"family_set": _is_family_set},
            frozenset({"GENERATE_CRITERIA: RawCriteria"}),
        ),
        Parser(
            "extract",
            "app.extract.parsing",
            _run_extract,
            {"source_dropped": _is_source_dropped},
            frozenset({"EXTRACT_CLAIMS: RawExtraction"}),
            reused=_extract_cases_from_p10,
        ),
        Parser(
            "research_reddit",
            "app.research.reddit.parsing",
            _run_reddit,
            {"items_skipped": _is_items_skipped},
            model_output=False,
        ),
    )
}


def file_cases(parser: str, golden: Path = GOLDEN) -> list[Case]:
    """The cases in this parser's folder. Read as bytes, so a byte-order mark or a CRLF stays in the output."""
    cases: list[Case] = []
    for raw in sorted((golden / parser / "cases").glob("*.raw")):
        expected = raw.with_suffix(".expected.json")
        if expected.exists():
            loaded = json.loads(expected.read_text(encoding="utf-8"))
            cases.append(Case(parser, raw.stem, raw.read_bytes().decode("utf-8"), loaded))
    return cases


def cases_of(parser: Parser, golden: Path = GOLDEN) -> list[Case]:
    return [*file_cases(parser.name, golden), *parser.reused()]


def all_cases() -> list[Case]:
    return [case for parser in PARSERS.values() for case in cases_of(parser)]


_PARSER_NAME = re.compile(r"pars(?:e|er|ers|ing)")


def parsing_modules(app: Path = APP) -> set[str]:
    """Every module under this app folder that is named as a parser, as a dotted name."""
    found: set[str] = set()
    for path in app.rglob("*.py"):
        relative = path.relative_to(app.parent).with_suffix("")
        if any(_PARSER_NAME.search(part) for part in relative.parts[1:]):
            found.add(".".join(relative.parts).removesuffix(".__init__"))
    return found


def _named(node: ast.expr | None) -> str:
    """A task or a schema as written: the last part of its name, or nothing if it isn't written out."""
    if isinstance(node, ast.Attribute):
        return node.attr
    return node.id if isinstance(node, ast.Name) else ""


def _call_key(call: ast.Call, where: str) -> str:
    task = call.args[0] if call.args else next((k.value for k in call.keywords if k.arg == "task"), None)
    schema = call.args[3] if len(call.args) > 3 else next((k.value for k in call.keywords if k.arg == "schema"), None)
    if not isinstance(task, ast.Attribute):  # a variable can't be checked against the registry
        return f"<task not written out in {where}>"
    return f"{task.attr}: {_named(schema)}"


def llm_calls_found(app: Path = APP) -> set[str]:
    """Each LLM call as "TASK: Schema". An LLM client's complete() passed around rather than called can't be checked."""
    found: set[str] = set()
    for path in app.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "complete":
                found.add(_call_key(node, path.name))
            elif isinstance(node, ast.Attribute) and node.attr == "complete" and id(node) not in called:
                found.add(f"<complete passed around in {path.name}>")
    return found


def unregistered_modules(app: Path = APP) -> set[str]:
    return parsing_modules(app) - {parser.module for parser in PARSERS.values()}


def unregistered_calls(app: Path = APP) -> set[str]:
    return llm_calls_found(app) - {call for parser in PARSERS.values() for call in parser.calls}


def parsers_short_of_cases(golden: Path = GOLDEN, minimum: int = MIN_CASES) -> dict[str, int]:
    counts = {parser.name: len(cases_of(parser, golden)) for parser in PARSERS.values()}
    return {name: count for name, count in counts.items() if count < minimum}


def kinds_missing(parser: Parser, golden: Path = GOLDEN) -> set[str]:
    return set(REQUIRED_KINDS) - {case.expected.get("kind", "") for case in cases_of(parser, golden)}


def holds_a_contract(result: Any) -> bool:
    return isinstance(result, BaseModel)
