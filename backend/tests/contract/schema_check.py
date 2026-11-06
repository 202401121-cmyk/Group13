"""A small JSON Schema checker for the contract tests: the keywords our exported schemas use, and no others.

The backend has no JSON Schema library; the frontend checks the same files with ajv. An unknown keyword raises, so a
new kind of rule in a schema can never be skipped without anyone noticing. `format` is only a note, as the JSON
Schema standard says, so it is not checked here.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

Schema = Mapping[str, Any]

NOTES = {"$defs", "default", "description", "format", "title"}
SCHEMA_TYPES: dict[str, Callable[[Any], bool]] = {
    "array": lambda v: isinstance(v, list),
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "null": lambda v: v is None,
    "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "string": lambda v: isinstance(v, str),
}


def schema_errors(value: Any, schema: Schema, root: Schema | None = None, where: str = "$") -> list[str]:
    """Every way `value` breaks `schema`, each with its path. An empty list means it is valid."""
    root = schema if root is None else root
    found: list[str] = []
    for keyword, rule in schema.items():
        if keyword not in NOTES:
            found += _check(keyword, rule, value, schema, root, where)
    return found


def _check(keyword: str, rule: Any, value: Any, schema: Schema, root: Schema, where: str) -> list[str]:
    if keyword == "$ref":
        return schema_errors(value, root["$defs"][rule.removeprefix("#/$defs/")], root, where)
    if keyword == "type":
        return [] if SCHEMA_TYPES[rule](value) else [f"{where}: not of type {rule}"]
    if keyword == "anyOf":
        return [] if any(not schema_errors(value, s, root, where) for s in rule) else [f"{where}: matches no option"]
    if keyword == "enum":
        return [] if value in rule else [f"{where}: not one of {rule}"]
    if keyword in ("properties", "patternProperties", "additionalProperties", "required"):
        return _check_object(keyword, rule, value, schema, root, where) if isinstance(value, dict) else []
    if keyword in ("items", "minItems", "maxItems"):
        return _check_array(keyword, rule, value, root, where) if isinstance(value, list) else []
    if keyword in ("minLength", "maxLength", "pattern"):
        return _check_string(keyword, rule, value, where) if isinstance(value, str) else []
    if keyword in ("minimum", "maximum", "exclusiveMinimum"):
        return _check_number(keyword, rule, value, where) if SCHEMA_TYPES["number"](value) else []
    raise ValueError(f"schema_check does not know the keyword {keyword!r}: add it here")


def _check_object(
    keyword: str, rule: Any, value: dict[str, Any], schema: Schema, root: Schema, where: str
) -> list[str]:
    if keyword == "required":
        return [f"{where}: {name} is missing" for name in rule if name not in value]
    if keyword == "properties":
        return [
            e
            for name, s in rule.items()
            if name in value
            for e in schema_errors(value[name], s, root, f"{where}.{name}")
        ]
    if keyword == "patternProperties":
        return [
            error
            for pattern, s in rule.items()
            for name, item in value.items()
            if re.search(pattern, name)
            for error in schema_errors(item, s, root, f"{where}.{name}")
        ]
    known = set(schema.get("properties", {}))
    extra = [name for name in value if name not in known and not _matches_a_pattern(name, schema)]
    if rule is False:
        return [f"{where}: {name} is not allowed" for name in extra]
    return (
        [] if rule is True else [e for name in extra for e in schema_errors(value[name], rule, root, f"{where}.{name}")]
    )


def _matches_a_pattern(name: str, schema: Schema) -> bool:
    return any(re.search(pattern, name) for pattern in schema.get("patternProperties", {}))


def _check_array(keyword: str, rule: Any, value: Sequence[Any], root: Schema, where: str) -> list[str]:
    if keyword == "items":
        return [e for i, item in enumerate(value) for e in schema_errors(item, rule, root, f"{where}[{i}]")]
    too_few = keyword == "minItems" and len(value) < rule
    too_many = keyword == "maxItems" and len(value) > rule
    return [f"{where}: {len(value)} items breaks {keyword} {rule}"] if too_few or too_many else []


def _check_string(keyword: str, rule: Any, value: str, where: str) -> list[str]:
    if keyword == "pattern":
        return [] if re.search(rule, value) else [f"{where}: does not match {rule}"]
    too_short = keyword == "minLength" and len(value) < rule
    too_long = keyword == "maxLength" and len(value) > rule
    return [f"{where}: length {len(value)} breaks {keyword} {rule}"] if too_short or too_long else []


def _check_number(keyword: str, rule: float, value: float, where: str) -> list[str]:
    fine = {"minimum": value >= rule, "maximum": value <= rule, "exclusiveMinimum": value > rule}[keyword]
    return [] if fine else [f"{where}: {value} breaks {keyword} {rule}"]
