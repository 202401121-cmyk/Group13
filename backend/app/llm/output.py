"""Turn the model's reply into a validated schema instance, or refuse it (US-26).

Accepted: a JSON object that is the whole reply, sits inside a ``` fence, or has prose around it.
Nothing is repaired. Trailing commas, bad quotes and truncation make the reply invalid, because guessing
what the model meant is how wrong data gets in (the team's rule: "never guess").

A call that reads retrieved text is stricter (US-28): the whole reply must be the object, or one fence holding only
it. Prose around it is refused, because a hostile page could have asked for it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.contracts import LLMTask

from .errors import InvalidOutput

M = TypeVar("M", bound=BaseModel)

JSON_INSTRUCTION = "Reply with only a JSON object that matches this JSON schema, and no other text:"

# Words that only make sense as an order to a model, in a field that should hold a name or a description.
_INSTRUCTION = re.compile(
    r"ignore\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier)\s+(?:instructions|rules|prompts?)"
    r"|disregard\s+(?:all\s+|the\s+)?(?:previous|prior|above)|system\s+prompt|you\s+are\s+now"
    r"|\b(?:always|must|should|please)\s+(?:\w+\s+){0,2}?rank\b.{0,60}\bfirst\b(?!-)"
    r"|^\s*rank\b.{0,60}\bfirst\b(?!-)",
    re.IGNORECASE,
)

_FENCE = re.compile(r"```[A-Za-z]*[ \t]*\n?(.*?)```", re.DOTALL)
_WHOLE_FENCE = re.compile(r"```[A-Za-z]*[ \t]*\n?(.*)```", re.DOTALL)

# The calls whose prompt holds retrieved text (build rule 3).
RETRIEVED_TEXT_TASKS: frozenset[LLMTask] = frozenset(
    {LLMTask.EXTRACT_CLAIMS, LLMTask.DISCOVER_ALIASES, LLMTask.ENRICH_EXTRACT, LLMTask.WRITE_EXPLANATION}
)


def looks_like_instruction(text: str) -> bool:
    return _INSTRUCTION.search(text) is not None


def build_system_prompt(system: str, schema: type[BaseModel]) -> str:
    """The caller's system prompt, ending with the one fixed instruction to answer in the schema's JSON."""
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    return f"{system.rstrip()}\n\n{JSON_INSTRUCTION}\n{schema_json}"


def parse_output(text: str, schema: type[M], *, strict: bool = False) -> M:
    """The reply as an instance of `schema`, or InvalidOutput. Never crashes on any input text."""
    obj = _find_object(text, strict)
    try:
        return schema.model_validate(obj)
    except ValidationError as exc:
        # The count only: the error text would repeat the reply's values.
        raise InvalidOutput(f"the reply does not match {schema.__name__} ({exc.error_count()} errors)") from None


def _find_object(text: str, strict: bool) -> dict[str, Any]:
    if not text.strip():
        raise InvalidOutput("the reply was empty")
    for candidate in _whole_reply(text.strip()) if strict else _candidates(text.strip()):
        try:
            value = json.loads(candidate)
        except (ValueError, RecursionError):  # not JSON, a number too long to read, or nested too deep to read
            continue
        if isinstance(value, dict):
            return value
        raise InvalidOutput(f"the reply is a JSON {type(value).__name__}, not an object")
    raise InvalidOutput("the reply has no complete JSON object")


def _whole_reply(text: str) -> Iterator[str]:
    """The whole reply, or what one fence holds if that fence is the whole reply."""
    yield text
    fence = _WHOLE_FENCE.fullmatch(text)
    if fence is not None:
        yield fence.group(1).strip()  # two fences read as one, which is not JSON


def _candidates(text: str) -> Iterator[str]:
    """Where the JSON object might be, in order: the whole reply, a fenced block, the first {...}."""
    yield text
    fence = _FENCE.search(text)
    inner = fence.group(1).strip() if fence else text
    if fence:
        yield inner
    first = _first_balanced_object(inner)
    if first is not None:
        yield first


def _first_balanced_object(text: str) -> str | None:
    """The first {...} whose braces balance, ignoring braces inside strings. None if it never closes."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None
