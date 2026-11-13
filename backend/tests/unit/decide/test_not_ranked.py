"""Nothing to rank on (S3-10b, US-18, DR-09): when no shown product has evidence on any criterion, none gets a score.

Unknown is never shown as 0. The products are listed by name, nobody is called the best match, and the decide stage's
note says why. Every product and request here is made up.
"""

from __future__ import annotations

from app.contracts import StageContext, StageName, StageStatus
from app.decide import WeightedDecideStage, decide
from app.decide.notes import NOT_RANKED_NOTE, not_ranked
from app.decide.stage import MAX_NOTES_KEPT
from app.pipeline import run_stage
from tests.unit.decide.build import decide_input, known, product, rubric, shown, unknown

A, B, C = "prd_alpha_one", "prd_bravo_two", "prd_charlie_3"
TWO = (("battery_life", 6.0), ("cpu_performance", 4.0))
SEARCH_ID = "srch_decide0001"


def _products() -> list:  # type: ignore[type-arg]
    return [product(C, "Zephyr Lite"), product(A, "Mango Air"), product(B, "Mango Air"), product("prd_dash_4", "Aster")]


def test_nothing_scored_gives_no_score_no_label_and_name_order() -> None:
    result = decide(decide_input(rubric(weights=TWO), _products(), []))
    assert shown(result) == ["prd_dash_4", A, B, C]  # Aster, Mango Air (ties by id), Zephyr Lite
    assert [r.rank for r in result.ranked] == [1, 2, 3, 4]
    for ranked in result.ranked:
        assert ranked.score is None
        assert ranked.label is None
        assert ranked.gap_to_leader == 0.0
        assert ranked.coverage == 0.0
        assert all(c.score is None and c.normalized_weight == 0.0 for c in ranked.contributions)
    assert not_ranked(result)


def test_unknown_values_alone_still_give_no_score() -> None:
    values = [unknown(A, "battery_life"), unknown(B, "cpu_performance"), known(A, "price_inr", value=60000)]
    result = decide(decide_input(rubric(weights=TWO), [product(A), product(B)], values))
    assert [r.score for r in result.ranked] == [None, None]


def test_one_score_anywhere_ranks_everyone_by_score() -> None:
    values = [known(B, "battery_life", score=6.0)]
    result = decide(decide_input(rubric(weights=TWO), [product(A), product(B)], values))
    assert all(r.score is not None for r in result.ranked)
    assert result.ranked[0].label == "best_match"
    assert not not_ranked(result)


def test_the_stage_gives_the_not_ranked_note_for_its_search_once() -> None:
    stage = WeightedDecideStage()
    inp = decide_input(rubric(weights=TWO), _products(), [])
    out, report = run_stage(stage, inp, StageContext(search_id=SEARCH_ID), 2.0)
    assert report.status is StageStatus.OK and report.stage is StageName.DECIDE
    assert out is not None and all(r.score is None for r in out.ranked)
    assert stage.coverage_note(SEARCH_ID) == NOT_RANKED_NOTE
    assert stage.coverage_note(SEARCH_ID) is None  # read once: the note is popped
    assert NOT_RANKED_NOTE == "Not ranked: we found no scores for your criteria."


def test_not_ranked_replaces_the_left_out_note() -> None:
    """With nothing scored every criterion is left out; saying so as well would only repeat the not-ranked note."""
    stage = WeightedDecideStage()
    stage.run(decide_input(rubric(weights=TWO), [product(A)], []), StageContext(search_id=SEARCH_ID))
    assert stage.coverage_note(SEARCH_ID) == NOT_RANKED_NOTE


def test_the_stage_gives_the_left_out_note_for_its_search() -> None:
    stage = WeightedDecideStage()
    values = [known(A, "battery_life", score=7.0)]
    stage.run(decide_input(rubric(weights=TWO), [product(A)], values), StageContext(search_id=SEARCH_ID))
    assert stage.coverage_note(SEARCH_ID) == (
        "We found no information on CPU performance for any product, so it was left out of the scores."
    )
    assert stage.coverage_note("srch_another001") is None  # another search's note is its own


def test_a_search_with_nothing_to_note_has_no_note() -> None:
    stage = WeightedDecideStage()
    values = [known(A, "battery_life", score=7.0), known(A, "cpu_performance", score=6.0)]
    stage.run(decide_input(rubric(weights=TWO), [product(A)], values), StageContext(search_id=SEARCH_ID))
    assert stage.coverage_note(SEARCH_ID) is None


def test_the_notes_kept_are_bounded_only_against_a_leak() -> None:
    """The pipeline reads a note right after decide; one never read (decide timed out) goes only past the bound."""
    stage = WeightedDecideStage()
    inp = decide_input(rubric(weights=TWO), [product(A)], [])
    for n in range(MAX_NOTES_KEPT + 1):
        stage.run(inp, StageContext(search_id=f"srch_bounded{n:05d}"))
    assert stage.coverage_note("srch_bounded00000") is None  # the oldest went
    assert stage.coverage_note("srch_bounded00001") == NOT_RANKED_NOTE  # every other one is still there
    assert stage.coverage_note(f"srch_bounded{MAX_NOTES_KEPT:05d}") == NOT_RANKED_NOTE


def test_the_bound_is_far_above_a_busy_servers_searches_at_once() -> None:
    assert MAX_NOTES_KEPT >= 1024
