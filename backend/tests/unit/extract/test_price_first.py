"""A public page asked for the price or a rule key gives it first (US-14, S4-1).

When a public prompt asks for price_inr or rule keys, those keys are listed first and the model is told to start with
them, for each product the page prices or specifies, then the criteria. Up to 5 more claims are allowed only where the
output limit holds them. One prompt goes to every model on the route, and Groq's 1,200-token public answer, the last
resort, holds none, so today a public page asks for 10. The parser trims only past the decision's limits: 15 claims in
all, at most 10 of them off the price and rule keys, which come first. Reddit prompts are unchanged. Every page and
product here is made up.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime

import pytest

from app.contracts import DataClass, ExtractInput, LLMTask, Source, SourceType, StageContext
from app.extract import GuardedExtractStage
from app.extract.parsing import RawClaim, RawExtraction, RawMention, to_extraction_result
from app.extract.prompts import (
    SYSTEM_PROMPT,
    PromptCriteria,
    SpecAsk,
    allowed_criteria,
    claim_room,
    groq_public_answer,
    public_answer_tokens,
    user_message,
)
from app.extract.settings import load_extract_settings
from tests.fakes.llm import FakeLLMClient
from tests.fakes.research import review_source, thread

SETTINGS = load_extract_settings(env={})
NOW = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
CTX = StageContext(search_id="srch_pricefirst")
# SYSTEM_PROMPT as it was before S4-1. Reddit calls, and public calls asked for no price or rule key, still send it.
SYSTEM_PROMPT_BEFORE = "772e753c5d8866119a18a3630bbdae722a628be66b8592fc1e2bc747234da7e6"

NAMES = ["Alto One", "Brio Two", "Cora Three", "Dune Four", "Echo Five", "Fable Six", "Gala Seven", "Halo Eight"]
NAMES += ["Iris Nine", "Juno Ten", "Kite Eleven", "Lumo Twelve"]
PRICES = [41990 + 2000 * n for n in range(len(NAMES))]
RAM = [16 if n % 2 else 8 for n in range(len(NAMES))]
TEXT = " ".join(
    f"{name} costs ₹{price:,}, has {ram} GB RAM, and its battery {'lasts all day' if n % 2 else 'is only average'}."
    for n, (name, price, ram) in enumerate(zip(NAMES, PRICES, RAM, strict=True))
)
PAGE = review_source("https://www.reviews.example/best-laptops", title="Made-up laptops under 70000", text=TEXT)
KEYS = ["battery_life", "price_inr", "ram_gb"]


def _criteria(
    source: Source = PAGE,
    *,
    has_budget: bool = False,
    attributes: tuple[str, ...] = (),
    features: tuple[str, ...] = (),
    answer_tokens: int | None = None,
) -> PromptCriteria:
    inp = ExtractInput(source=source, criterion_keys=["battery_life"], category_label="laptop")
    data_class = DataClass.REDDIT if source.type is SourceType.REDDIT else DataClass.PUBLIC
    return allowed_criteria(inp, data_class, SpecAsk(attributes, has_budget, features), SETTINGS, answer_tokens)


def _price(n: int) -> RawClaim:
    words = f"{NAMES[n]} costs ₹{PRICES[n]:,}"
    return RawClaim(
        product_mention=NAMES[n], criterion_key="price_inr", value=words[-7:], quote=words, polarity="neutral"
    )


def _ram(n: int) -> RawClaim:
    return RawClaim(
        product_mention=NAMES[n], criterion_key="ram_gb", value=RAM[n], unit="GB", quote=f"has {RAM[n]} GB RAM",
        polarity="neutral",
    )  # fmt: skip


def _battery(n: int) -> RawClaim:
    good = n % 2 == 1
    return RawClaim(
        product_mention=NAMES[n],
        criterion_key="battery_life",
        score=8.0 if good else 5.0,
        quote="its battery lasts all day" if good else "its battery is only average",
        polarity="positive" if good else "neutral",
    )


def _raw(claims: list[RawClaim]) -> RawExtraction:
    return RawExtraction(mentions=[RawMention(raw_name=name, is_product=True) for name in NAMES], claims=claims)


def _parse(
    claims: list[RawClaim], room: int | None = None, others: int | None = None, first: tuple[str, ...] = ()
) -> list[tuple[str | None, str]]:
    result = to_extraction_result(
        _raw(claims), PAGE, KEYS, NOW, category="laptop", room=room, others=others, first=first
    )
    return [(c.criterion_key, c.product_mention) for c in result.claims]


def _drops(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if "dropped from" in r.getMessage()]


# --- the prompt ------------------------------------------------------------------------------------------------------


def test_a_public_page_with_a_budget_asks_for_the_price_first() -> None:
    criteria = _criteria(has_budget=True)
    assert criteria.first == ("price_inr",)
    assert criteria.room == 10  # Groq's public answer can't hold more: see the next tests
    system = criteria.system
    assert system != SYSTEM_PROMPT
    assert (
        "at most 10, starting with the first key for each product priced or specified, then the most useful." in system
    )
    assert criteria.lines[0].startswith("- price_inr: ")  # the key to give first is listed first
    message = user_message(PAGE, criteria)
    assert message.index("- price_inr: ") < message.index("- battery_life: ") < message.index("<untrusted_data")


def test_where_every_answer_holds_them_the_room_is_15() -> None:
    criteria = _criteria(has_budget=True, answer_tokens=2000)
    assert criteria.room == 15
    assert "at most 15, starting with the first key for each product priced or specified" in criteria.system
    assert "at most 10" not in criteria.system


def test_groqs_public_answer_keeps_the_room_at_10() -> None:
    """One prompt goes to every model on a public page's route; Groq, the last resort, answers in 1,200 tokens."""
    assert groq_public_answer() == 1200
    assert public_answer_tokens(SETTINGS) == 1200 < SETTINGS.output_tokens(DataClass.PUBLIC)
    assert claim_room(("price_inr",), public_answer_tokens(SETTINGS)) == 10


def test_rule_keys_come_first_after_the_price() -> None:
    criteria = _criteria(has_budget=True, attributes=("ram_gb", "weight_kg"))
    assert criteria.first[:2] == ("price_inr", "ram_gb")
    assert criteria.keys[: len(criteria.first)] == criteria.first  # listed first, in that order
    assert f"starting with the first {len(criteria.first)} keys for each product priced or specified" in criteria.system
    assert criteria.room == 10
    rules_only = _criteria(attributes=("ram_gb",), answer_tokens=2000)
    assert rules_only.first == ("ram_gb",) and rules_only.room == 15


def test_the_15_claim_room_is_used_only_when_asked_for_price_or_rule_keys() -> None:
    plain = _criteria(answer_tokens=2000)  # even where the answer would hold 15
    assert (plain.first, plain.room, plain.system) == ((), 10, SYSTEM_PROMPT)
    assert plain.keys == ("battery_life",)
    features_only = _criteria(features=("touchscreen",), answer_tokens=2000)  # asked for, but not first
    assert any(k.startswith("has_") for k in features_only.keys)
    assert (features_only.first, features_only.room, features_only.system) == ((), 10, SYSTEM_PROMPT)


def test_the_room_needs_the_output_limit_to_hold_it() -> None:
    assert claim_room(("price_inr",), 2000) == 15  # a public page asks for 2,000 tokens
    assert claim_room(("price_inr",), 1500) == 12
    assert claim_room(("price_inr",), 1200) == 10  # Groq's public answer holds no extra claim
    assert claim_room(("price_inr",), 800) == 10  # never fewer than before
    assert claim_room((), 2000) == 10


def test_an_unreadable_llm_config_counts_as_groqs_1200(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.extract.prompts as prompts

    def unreadable() -> object:
        raise prompts.LLMConfigError("cannot read LLM config")

    groq_public_answer.cache_clear()
    monkeypatch.setattr(prompts, "load_llm_config", unreadable)
    try:
        assert groq_public_answer() == prompts.GROQ_PUBLIC_ANSWER == 1200
        assert public_answer_tokens(SETTINGS) == 1200
    finally:
        groq_public_answer.cache_clear()  # the next test reads the real config again


# --- the stage -------------------------------------------------------------------------------------------------------


def test_a_reddit_prompt_is_unchanged_and_its_claims_are_never_trimmed() -> None:
    assert hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest() == SYSTEM_PROMPT_BEFORE
    reddit = thread("abc123", title="A made-up thread", text=TEXT)
    criteria = _criteria(reddit, has_budget=True, attributes=("ram_gb",))
    assert (criteria.first, criteria.room, criteria.system) == ((), 10, SYSTEM_PROMPT)
    assert criteria.keys == ("battery_life",)  # the search's own keys, as before: no price, no rule key
    client = FakeLLMClient({LLMTask.EXTRACT_CLAIMS: _raw([_battery(n) for n in range(12)])})
    stage = GuardedExtractStage(client, SETTINGS, lambda: NOW)
    stage.ask_for_specs(CTX.search_id, ["ram_gb"], has_budget=True)
    out = stage.run(ExtractInput(source=reddit, criterion_keys=["battery_life"], category_label="laptop"), CTX)
    (call,) = client.calls
    assert call.system == SYSTEM_PROMPT
    assert call.user == user_message(reddit, criteria)
    assert call.max_output_tokens == SETTINGS.output_tokens(DataClass.REDDIT)
    assert len(out.claims) == 12  # past 10, all kept: nothing trims a Reddit answer


def test_a_public_page_asked_for_no_price_or_rule_is_never_trimmed() -> None:
    client = FakeLLMClient({LLMTask.EXTRACT_CLAIMS: _raw([_battery(n) for n in range(12)])})
    stage = GuardedExtractStage(client, SETTINGS, lambda: NOW)
    out = stage.run(ExtractInput(source=PAGE, criterion_keys=["battery_life"], category_label="laptop"), CTX)
    (call,) = client.calls
    assert call.system == SYSTEM_PROMPT
    assert len(out.claims) == 12


def test_the_stage_sends_the_price_first_prompt_and_trims_past_the_limits() -> None:
    claims = [_battery(n) for n in range(8)] + [_price(n) for n in range(8)]  # 16: one past 15 in all
    client = FakeLLMClient({LLMTask.EXTRACT_CLAIMS: _raw(claims)})
    stage = GuardedExtractStage(client, SETTINGS, lambda: NOW)
    stage.ask_for_specs(CTX.search_id, [], has_budget=True)
    out = stage.run(ExtractInput(source=PAGE, criterion_keys=["battery_life"], category_label="laptop"), CTX)
    (call,) = client.calls
    assert "at most 10, starting with the first key for each product priced or specified" in call.system
    assert call.user.index("- price_inr: ") < call.user.index("- battery_life: ")
    assert call.max_output_tokens == SETTINGS.output_tokens(DataClass.PUBLIC)
    assert [c.criterion_key for c in out.claims] == ["price_inr"] * 8 + ["battery_life"] * 7


def test_a_public_answer_within_the_limits_keeps_every_claim_through_the_stage() -> None:
    claims = [_battery(n) for n in range(4)] + [_price(n) for n in range(10)]  # 14: past the prompt's 10, but whole
    client = FakeLLMClient({LLMTask.EXTRACT_CLAIMS: _raw(claims)})
    stage = GuardedExtractStage(client, SETTINGS, lambda: NOW)
    stage.ask_for_specs(CTX.search_id, [], has_budget=True)
    out = stage.run(ExtractInput(source=PAGE, criterion_keys=["battery_life"], category_label="laptop"), CTX)
    assert [c.criterion_key for c in out.claims] == ["battery_life"] * 4 + ["price_inr"] * 10  # as the model gave


# --- trimming in the parser ------------------------------------------------------------------------------------------


def test_trimming_keeps_price_and_rule_claims_first(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.extract.parsing")
    claims = [_battery(n) for n in range(10)] + [_ram(n) for n in range(4)] + [_price(n) for n in range(8)]  # 22
    kept = _parse(claims, room=15, others=10, first=("price_inr", "ram_gb"))
    assert [key for key, _ in kept] == ["price_inr"] * 8 + ["ram_gb"] * 4 + [
        "battery_life"
    ] * 3  # the price, then rules
    assert [name for _, name in kept[:8]] == NAMES[:8]
    assert [name for _, name in kept[12:]] == NAMES[:3]  # the criteria keep the model's own order
    assert _drops(caplog) == ["extract: claims dropped from a review_site source: {'room': 7}"]  # no price dropped


def test_past_the_room_a_price_drop_is_counted_for_the_funnel(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.extract.parsing")
    kept = _parse([_price(n) for n in range(12)], room=10, others=10, first=("price_inr",))
    assert len(kept) == 10
    assert _drops(caplog) == [
        "extract: claims dropped from a review_site source: {'room': 2}",
        "extract: price claims dropped from a review_site source: {'room': 2}",
    ]


def test_the_criteria_keep_at_most_10_when_keys_come_first() -> None:
    claims = [_battery(n) for n in range(12)] + [_price(n) for n in range(2)]  # 14: within 15, but 12 criteria
    kept = _parse(claims, room=15, others=10, first=("price_inr",))
    assert [key for key, _ in kept] == ["price_inr"] * 2 + ["battery_life"] * 10  # the 5 above 10 are the price's


def test_within_the_limits_nothing_is_trimmed_or_reordered(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.extract.parsing")
    claims = [_battery(n) for n in range(4)] + [_price(n) for n in range(8)]
    kept = _parse(claims, room=15, others=10, first=("price_inr",))
    assert [key for key, _ in kept] == ["battery_life"] * 4 + ["price_inr"] * 8  # as the model gave
    assert _drops(caplog) == []


def test_with_no_room_nothing_is_trimmed() -> None:
    claims = [_battery(n) for n in range(12)] + [_price(n) for n in range(12)]
    assert len(_parse(claims)) == 24  # Reddit, or a public page asked for no price or rule: as before S4-1
