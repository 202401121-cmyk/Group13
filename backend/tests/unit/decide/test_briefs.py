"""Five made-up briefs through the decide stage (US-17, US-18), and a recorded run replayed through it.

The briefs live in tests/fixtures/decide/. Each one lists, written by hand, the products it expects to see shown (in
rank order), filtered out and can't-tell, with their reasons. The rule check below is written separately from the
stage's own, so a bug in app/decide/ can't hide itself.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from app.contracts import (
    CriteriaInput,
    DecideInput,
    DecideResult,
    IntakeInput,
    InterviewState,
    Provider,
    ResolvedValue,
    StageContext,
    ValueState,
)
from app.criteria import CriteriaStage, InMemoryCriteriaStore
from app.decide import WeightedDecideStage, decide
from app.decide.notes import left_out
from app.intake import IntakeStage
from app.interview.rubric_builder import default_rubric
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.llm.providers import Adapter
from tests.fakes.recorded import RecordedAdapter, load_recording

BRIEFS = Path(__file__).resolve().parents[2] / "fixtures" / "decide"
NAMES = sorted(p.stem for p in BRIEFS.glob("*.json"))
CTX = StageContext(search_id="srch_briefs0001")


@cache
def _brief(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((BRIEFS / f"{name}.json").read_text(encoding="utf-8"))
    return data


def _input(name: str) -> DecideInput:
    return DecideInput.model_validate(_brief(name)["input"])


def _groups(result: DecideResult) -> dict[str, Any]:
    return {
        "shown": [r.product_id for r in result.ranked],
        "filtered_out": {f.product_id: list(f.reasons) for f in result.filtered if f.group == "filtered_out"},
        "cant_tell": {f.product_id: list(f.reasons) for f in result.filtered if f.group == "cant_tell"},
        "left_out": left_out(result),
        "relaxation_hint": result.relaxation_hint,
    }


def test_there_are_five_made_up_briefs() -> None:
    assert NAMES == [
        "ereader_8k",
        "headphones_anc_range",
        "laptop_coding_70k",
        "monitor_27_gaming",
        "phone_camera_30k",
    ]
    for name in NAMES:
        about = _brief(name)["about"].lower()
        assert "made-up" in about and "synthetic" in about
    assert "made up" in (BRIEFS / "README.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", NAMES)
def test_each_brief_gives_its_expected_groups(name: str) -> None:
    result = WeightedDecideStage().run(_input(name), CTX)
    assert _groups(result) == _brief(name)["expect"]
    assert DecideResult.model_validate_json(result.model_dump_json()) == result


# --- an independent check of every shown product -------------------------------------------------------------------

# Written apart from app/decide/units.py on purpose: only what the five briefs use.
_TO_BASE = {"gb": 1.0, "tb": 1000.0, "g": 1.0, "kg": 1000.0, "oz": 28.349523125, "cm": 1.0, "inch": 2.54, "in": 2.54}
_TO_BASE |= {"hz": 1.0, "khz": 1000.0}
_SUFFIX_UNIT = {"ram_gb": "gb", "weight_g": "g", "screen_size_inch": "inch", "refresh_rate_hz": "hz"}


def _number(value: object, attribute: str) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value) * _TO_BASE[_SUFFIX_UNIT[attribute]]
    match = re.fullmatch(r"\s*([\d.]+)\s*([a-zA-Z]+)\s*", str(value))
    assert match, f"the check does not read {value!r}"
    return float(match[1]) * _TO_BASE[match[2].lower()]


def _truth(value: object) -> bool:
    if isinstance(value, bool):
        return value
    assert isinstance(value, str) and value.lower() in {"yes", "no"}
    return value.lower() == "yes"


def _breaks(inp: DecideInput, pid: str, values: dict[str, ResolvedValue]) -> list[str]:
    """Every rule this product breaks, or can't be shown to keep."""
    broken: list[str] = []
    item = next(p for p in inp.products if p.id == pid)
    budget = inp.rubric.budget
    if budget is not None:
        price = values.get("price_inr")
        if price is None or price.state is not ValueState.KNOWN or not isinstance(price.value, int | float):
            broken.append("price unknown")
        else:
            if budget.max is not None and price.value > budget.max.amount:
                broken.append("above the budget")
            if budget.min is not None and price.value < budget.min.amount:
                broken.append("below the minimum")
    for rule in inp.rubric.hard_rules:
        if not rule.active:
            continue
        value = values.get(rule.attribute)
        if value is None or value.state is not ValueState.KNOWN or value.value is None:
            broken.append(f"{rule.attribute} unknown")
        elif isinstance(rule.value, bool):
            if _truth(value.value) != rule.value:
                broken.append(rule.source_text)
        else:
            assert isinstance(rule.value, int | float) and rule.unit is not None
            limit = float(rule.value) * _TO_BASE[rule.unit.lower()]
            have = _number(value.value, rule.attribute)
            if (rule.operator == ">=" and have < limit - 1e-6) or (rule.operator == "<=" and have > limit + 1e-6):
                broken.append(rule.source_text)
    words = item.name.lower().split()
    for exclusion in inp.rubric.exclusions:
        target = exclusion.value.lower()
        if exclusion.kind == "brand" and (target == (item.brand or "").lower() or words[:1] == [target]):
            broken.append(f"brand {exclusion.value}")
        if exclusion.kind == "model" and target in item.name.lower():
            broken.append(f"model {exclusion.value}")
        if exclusion.kind == "feature":
            key = target.replace(" ", "_")
            for name in (key, f"has_{key}", f"is_{key}"):
                found = values.get(name)
                if found is not None and found.state is ValueState.KNOWN and found.value is True:
                    broken.append(f"feature {exclusion.value}")
    return broken


@pytest.mark.parametrize("name", NAMES)
def test_no_shown_product_breaks_a_rule_in_any_brief(name: str) -> None:
    inp = _input(name)
    result = decide(inp)
    for ranked in result.ranked:
        values = {v.criterion_key: v for v in inp.values if v.product_id == ranked.product_id}
        assert _breaks(inp, ranked.product_id, values) == [], ranked.product_id
    for item in result.filtered:  # and the check itself is not blind: every product held back breaks something
        values = {v.criterion_key: v for v in inp.values if v.product_id == item.product_id}
        assert _breaks(inp, item.product_id, values), item.product_id


# --- a recorded run, replayed through the stage ----------------------------------------------------------------------


def _recorded_rubric() -> Any:
    """The rubric from the real intake and criteria replies recorded for "best laptop for coding under 70k"."""
    recording = load_recording("laptop_coding_70k")
    adapters: dict[Provider, Adapter] = {Provider.GROQ: RecordedAdapter(recording, Provider.GROQ)}
    llm = WrapperLLMClient(load_llm_config(), adapters, InMemoryCallLog(), limiters={})
    intent = IntakeStage(llm).run(IntakeInput(raw_query=recording.request), CTX)
    assert intent.category is not None and intent.category_label is not None
    criteria = CriteriaStage(llm, InMemoryCriteriaStore()).run(
        CriteriaInput(category=intent.category, category_label=intent.category_label, use_case=intent.use_case), CTX
    )
    return default_rubric(InterviewState(session_id="sess_replay0001", intent=intent, criteria=criteria))


def test_a_recorded_run_replays_through_the_stage() -> None:
    """S3-6 is not merged, so the values are the laptop brief's made-up ones; the rubric is the recorded run's."""
    rubric = _recorded_rubric()
    brief = _input("laptop_coding_70k")
    assert rubric.budget is not None and rubric.budget.max is not None and rubric.budget.max.amount == 70000
    assert rubric.hard_rules == [] and rubric.exclusions == []  # the reply's price rule is the budget, read by code
    assert [w.key for w in rubric.weights] == [w.key for w in brief.rubric.weights]
    result = WeightedDecideStage().run(DecideInput(rubric=rubric, products=brief.products, values=brief.values), CTX)
    groups = _groups(result)
    assert groups["filtered_out"] == {"prd_aurora_air_13": ["₹84,990 is above your ₹70,000 budget"]}
    assert set(groups["cant_tell"]) == {"prd_lumen_x_15", "prd_tern_book_16"}
    assert groups["shown"][0] == "prd_pico_lite_14"  # its 8 GB of RAM breaks no rule here: the recording had none
    assert len(groups["shown"]) == 6
    assert groups["left_out"] == ["max_temperature"]
