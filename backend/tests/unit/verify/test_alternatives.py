"""A disputed value keeps its other side (S3-10b, DR-03): the outvoted value or score, its unit and its claims.

Decide reads both sides, so a price disputed across the budget is "can't tell" instead of a guess. Every site, product
and claim here is made up.
"""

from __future__ import annotations

from app.contracts import AlternativeValue, SourceType, StageContext, ValueState, VerifyResult
from app.verify import TrustVerifyStage
from tests.unit.verify.build import claim, match, source, value_of, verify_input

NOVA = "prd_nova_book_14"


def _run(inp: object) -> VerifyResult:
    return TrustVerifyStage().run(inp, StageContext(search_id="srch_verify0002"))  # type: ignore[arg-type]


def _pages(n: int) -> list:  # type: ignore[type-arg]
    return [source(f"https://{chr(ord('a') + i)}.example/nova-book-14") for i in range(n)]


def test_a_disputed_number_keeps_the_other_value_with_its_unit_and_claims() -> None:
    a, b = _pages(2)
    claims = [
        claim(a, "Nova Book 14", "battery_life", value=10, unit="hours"),
        claim(b, "Nova Book 14", "battery_life", value=6, unit="hours"),
    ]
    battery = value_of(_run(verify_input(match("Nova Book 14"), claims, [a, b])), NOVA, "battery_life")
    assert battery.state is ValueState.DISPUTED
    [other] = battery.alternatives
    won = 10.0 if battery.supporting_claim_ids == [claims[0].id] else 6.0
    lost = claims[1] if won == 10.0 else claims[0]
    assert other == AlternativeValue(value=16.0 - won, unit="hours", claim_ids=[lost.id])
    assert battery.conflicting_claim_ids == [lost.id]


def test_a_disputed_price_keeps_both_prices() -> None:
    a, b = _pages(2)
    claims = [
        claim(a, "Nova Book 14", "price_inr", value=68990, unit="INR"),
        claim(b, "Nova Book 14", "price_inr", value=74990, unit="INR"),
    ]
    price = value_of(_run(verify_input(match("Nova Book 14"), claims, [a, b])), NOVA, "price_inr")
    assert price.state is ValueState.DISPUTED
    [other] = price.alternatives
    assert {str(price.value), f"{other.value:g} {other.unit}"} == {"68990 INR", "74990 INR"}


def test_a_disputed_score_keeps_the_other_score() -> None:
    a, b = _pages(2)
    claims = [
        claim(a, "Nova Book 14", "battery_life", score=9.0),
        claim(b, "Nova Book 14", "battery_life", score=4.0),
    ]
    battery = value_of(_run(verify_input(match("Nova Book 14"), claims, [a, b])), NOVA, "battery_life")
    assert battery.state is ValueState.DISPUTED
    [other] = battery.alternatives
    assert other.value is None and other.unit is None
    assert other.score in (9.0, 4.0)
    assert other.claim_ids == battery.conflicting_claim_ids


def test_disputed_text_keeps_the_other_text_and_no_unit() -> None:
    a, b = _pages(2)
    claims = [
        claim(a, "Nova Book 14", "wifi", value="Wi-Fi 6"),
        claim(b, "Nova Book 14", "wifi", value="Wi-Fi 6E"),
    ]
    wifi = value_of(_run(verify_input(match("Nova Book 14"), claims, [a, b], ["wifi"])), NOVA, "wifi")
    assert wifi.state is ValueState.DISPUTED
    [other] = wifi.alternatives
    assert {wifi.value, other.value} == {"Wi-Fi 6", "Wi-Fi 6E"}
    assert other.unit is None


def test_two_different_losing_values_are_two_alternatives() -> None:
    a, b, c = _pages(3)
    claims = [
        claim(a, "Nova Book 14", "battery_life", value=10, unit="hours"),
        claim(b, "Nova Book 14", "battery_life", value=6, unit="hours"),
        claim(c, "Nova Book 14", "battery_life", value=3, unit="hours"),
    ]
    battery = value_of(_run(verify_input(match("Nova Book 14"), claims, [a, b, c])), NOVA, "battery_life")
    assert battery.state is ValueState.DISPUTED
    assert len(battery.alternatives) == 2
    cited = sorted(i for alt in battery.alternatives for i in alt.claim_ids)
    assert cited == battery.conflicting_claim_ids
    main = float(str(battery.value).split()[0])  # "6 hours": shown as the sources wrote it
    assert sorted([main, *(float(str(alt.value)) for alt in battery.alternatives)]) == [3.0, 6.0, 10.0]
    assert all(alt.unit == "hours" for alt in battery.alternatives)


def test_a_known_value_has_no_alternatives() -> None:
    maker = source("https://nova.example/book-14/specs", SourceType.MANUFACTURER)
    review = source("https://reviews.example/nova-book-14")
    claims = [
        claim(maker, "Nova Book 14", "ram_gb", value=16, unit="GB"),
        claim(review, "Nova Book 14", "ram_gb", value="16 GB"),
    ]
    ram = value_of(_run(verify_input(match("Nova Book 14"), claims, [maker, review])), NOVA, "ram_gb")
    assert ram.state is ValueState.KNOWN
    assert ram.alternatives == []


def test_the_same_claims_in_any_order_give_the_same_alternatives() -> None:
    a, b, c = _pages(3)
    claims = [
        claim(a, "Nova Book 14", "battery_life", value=10, unit="hours"),
        claim(b, "Nova Book 14", "battery_life", value=6, unit="hours"),
        claim(c, "Nova Book 14", "battery_life", value=3, unit="hours"),
    ]
    first = _run(verify_input(match("Nova Book 14"), claims, [a, b, c]))
    again = _run(verify_input(match("Nova Book 14"), claims[::-1], [c, b, a]))
    assert first == again


def test_a_bare_number_alternative_carries_the_unit_it_was_compared_in() -> None:
    """A bare 512 among "1 TB" claims was compared as 512 TB, so decide must read it as TB too."""
    a, b = _pages(2)
    claims = [
        claim(a, "Nova Book 14", "storage", value="1 TB"),
        claim(b, "Nova Book 14", "storage", value=512),
    ]
    storage = value_of(_run(verify_input(match("Nova Book 14"), claims, [a, b], ["storage"])), NOVA, "storage")
    assert storage.state is ValueState.DISPUTED
    assert storage.value == "1 TB"
    [other] = storage.alternatives
    assert (other.value, other.unit, other.claim_ids) == (512.0, "TB", [claims[1].id])
