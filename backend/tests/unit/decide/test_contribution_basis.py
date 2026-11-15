"""Each contribution says how its criterion score was made (S4-0, contracts 1.9.0): a source's opinion, a stated
value compared with the other shown products' (P-20, US-18), or the peers' average (DR-09). With no score, no basis.
Every product and value here is made up."""

from __future__ import annotations

from app.contracts import Contribution, DecideResult, ResolvedValue, ValueState
from app.decide import decide
from tests.unit.decide.build import decide_input, estimated, known, product, rubric

A, B, C = "prd_alpha_one", "prd_bravo_two", "prd_charlie_three"


def _run(
    weights: list[tuple[str, float]], values: list[ResolvedValue], pids: tuple[str, ...] = (A, B, C)
) -> DecideResult:
    return decide(decide_input(rubric(weights=weights), [product(p) for p in pids], values))


def _of(result: DecideResult, pid: str, key: str) -> Contribution:
    ranked = next(r for r in result.ranked if r.product_id == pid)
    return next(c for c in ranked.contributions if c.criterion_key == key)


def test_an_opinion_score_has_basis_opinion() -> None:
    values = [known(p, "battery_life", score=s) for p, s in ((A, 6.0), (B, 7.0), (C, 8.0))]
    result = _run([("battery_life", 5.0)], values)
    assert {_of(result, p, "battery_life").basis for p in (A, B, C)} == {"opinion"}
    assert _of(result, C, "battery_life").score == 8.0


def test_a_compared_value_has_basis_compared() -> None:
    values = [known(A, "battery_life", value="10 hours"), known(B, "battery_life", value="15 hours")]
    values += [known(C, "battery_life", value="20 hours")]
    result = _run([("battery_life", 5.0)], values)
    assert {_of(result, p, "battery_life").basis for p in (A, B, C)} == {"compared"}
    assert _of(result, C, "battery_life").score == 9.0


def test_an_opinion_wins_over_a_value_and_keeps_basis_opinion() -> None:
    values = [known(A, "battery_life", score=4.0, value="20 hours"), known(B, "battery_life", value="10 hours")]
    values += [known(C, "battery_life", value="15 hours")]
    result = _run([("battery_life", 5.0)], values)
    assert _of(result, A, "battery_life").basis == "opinion"
    assert _of(result, A, "battery_life").score == 4.0


def test_a_peer_average_has_basis_estimated() -> None:
    values = [known(A, "battery_life", score=6.0), known(B, "battery_life", score=8.0)]
    result = _run([("battery_life", 5.0)], values)
    estimate = _of(result, C, "battery_life")
    assert (estimate.basis, estimate.state, estimate.score) == ("estimated", ValueState.ESTIMATED, 7.0)
    assert _of(result, A, "battery_life").basis == "opinion"


def test_an_estimate_from_verify_has_basis_estimated() -> None:
    """Verify's ESTIMATED value is already a peer average (DR-09): it keeps its score and is labelled estimated."""
    values = [
        known(A, "battery_life", score=6.0),
        estimated(B, "battery_life", 5.5),
        known(C, "battery_life", score=8.0),
    ]
    result = _run([("battery_life", 5.0)], values)
    from_verify = _of(result, B, "battery_life")
    assert (from_verify.basis, from_verify.state, from_verify.score) == ("estimated", ValueState.ESTIMATED, 5.5)
    assert {_of(result, p, "battery_life").basis for p in (A, C)} == {"opinion"}


def test_a_criterion_with_no_weight_has_no_basis() -> None:
    """A criterion no shown product has evidence on is left out of the scores: no score, so no basis."""
    values = [known(p, "battery_life", score=s) for p, s in ((A, 6.0), (B, 7.0), (C, 8.0))]
    result = _run([("battery_life", 5.0), ("keyboard_comfort", 5.0)], values)
    for pid in (A, B, C):
        left_out = _of(result, pid, "keyboard_comfort")
        assert (left_out.score, left_out.basis, left_out.normalized_weight) == (None, None, 0.0)
        assert _of(result, pid, "battery_life").basis == "opinion"


def test_nothing_to_rank_on_gives_no_basis() -> None:
    result = _run([("battery_life", 5.0)], [])
    assert result.ranked and all(r.score is None for r in result.ranked)
    assert {c.basis for r in result.ranked for c in r.contributions} == {None}
