"""The interview's beliefs about each weight (US-04, D-69): a normal belief, updated in code by the normal-normal rule.

The numbers in these tests are worked out by hand in the comments, so the rule itself is checked, not just the code.
"""

from __future__ import annotations

import math

import pytest

from app.contracts import (
    ROUGH_LEVEL_WEIGHTS,
    AskedQuestion,
    InterviewState,
    ReplyClassification,
    ReplyType,
    RoughLevel,
    WeightBelief,
)
from app.contracts import examples as ex
from app.interview.beliefs import (
    LEVEL_SPREADS,
    BeliefSettings,
    answer_sd,
    beliefs_for,
    level_spread,
    rough_level,
    updated,
)
from app.interview.questions import question_for

SETTINGS = BeliefSettings(prior_sd=2.5, exact_sd=1.5, settled_sd=1.2)  # D-69's own numbers, for hand-computed checks


def answered(key: str, weight: float, reply_text: str | None) -> AskedQuestion:
    criterion = ex.criteria().get(key)
    assert criterion is not None
    return AskedQuestion(
        question=question_for(criterion),
        reply_text=reply_text,
        classification=ReplyClassification(reply_type=ReplyType.ANSWER, weight=weight, classified_by="fallback"),
        status="answered",
    )


def entry(key: str, status: str) -> AskedQuestion:
    criterion = ex.criteria().get(key)
    assert criterion is not None
    return AskedQuestion(question=question_for(criterion), status=status)  # type: ignore[arg-type]


def state(*history: AskedQuestion) -> InterviewState:
    return InterviewState(session_id=ex.SESSION_ID, intent=ex.intent(), criteria=ex.criteria(), history=list(history))


# --- the update rule ------------------------------------------------------------------------------------------------


def test_one_answer_updates_the_belief_by_the_normal_normal_rule() -> None:
    # prior mean 5, sd 2.5; an exact answer of 8 with noise sd 1.5
    # precisions: 1 / 2.5^2 = 0.16 and 1 / 1.5^2 = 0.444444; together 0.604444, so the variance is 1.654412
    # sd = sqrt(1.654412) = 1.286240; mean = 1.654412 * (5 * 0.16 + 8 * 0.444444) = 1.654412 * 4.355556 = 7.205882
    after = updated(WeightBelief(mean=5.0, sd=2.5), 8.0, 1.5)
    assert after.mean == pytest.approx(7.205882, abs=1e-6)
    assert after.sd == pytest.approx(1.286240, abs=1e-6)


def test_a_second_answer_narrows_it_again_whatever_the_order() -> None:
    # 5 at sd 2.5, then 2 at sd 1.0: precisions 0.16 + 1 = 1.16, variance 0.862069, sd 0.928477,
    # mean = 0.862069 * (5 * 0.16 + 2 * 1) = 0.862069 * 2.8 = 2.413793
    one = updated(WeightBelief(mean=5.0, sd=2.5), 2.0, 1.0)
    assert (one.mean, one.sd) == (pytest.approx(2.413793, abs=1e-6), pytest.approx(0.928477, abs=1e-6))
    a = updated(updated(WeightBelief(mean=5.0, sd=2.5), 8.0, 1.5), 2.0, 1.0)
    b = updated(updated(WeightBelief(mean=5.0, sd=2.5), 2.0, 1.0), 8.0, 1.5)
    assert (a.mean, a.sd) == (pytest.approx(b.mean), pytest.approx(b.sd))
    assert a.sd < one.sd  # every answer makes the app surer


def test_a_surer_answer_moves_the_mean_further() -> None:
    prior = WeightBelief(mean=5.0, sd=2.5)
    assert updated(prior, 9.0, 0.5).mean > updated(prior, 9.0, 1.5).mean > prior.mean


def test_the_mean_stays_on_the_0_to_10_scale_at_the_ends() -> None:
    assert updated(WeightBelief(mean=10.0, sd=2.5), 10.0, 0.5).mean <= 10.0
    assert updated(WeightBelief(mean=0.0, sd=2.5), 0.0, 0.5).mean >= 0.0


# --- how sure an answer is ------------------------------------------------------------------------------------------


def test_each_levels_spread_comes_from_the_us08_table_alone() -> None:
    # A level stands for the weights nearer to it than to any other level; its spread is how far those weights sit
    # from the level's weight, as a root mean square (weights spread evenly). Levels: low 2, good enough 3, medium 5,
    # high 8, so the borders are 2.5, 4 and 6.5, and the scale ends at 0 and 10.
    # low, 0 to 2.5:      sqrt(((2.5 - 2)^3 - (0 - 2)^3) / (3 * 2.5))     = sqrt(8.125 / 7.5)   = 1.040833
    # good enough, 2.5-4: sqrt(((4 - 3)^3 - (2.5 - 3)^3) / (3 * 1.5))     = sqrt(1.125 / 4.5)   = 0.5
    # medium, 4 to 6.5:   sqrt(((6.5 - 5)^3 - (4 - 5)^3) / (3 * 2.5))     = sqrt(4.375 / 7.5)   = 0.763763
    # high, 6.5 to 10:    sqrt(((10 - 8)^3 - (6.5 - 8)^3) / (3 * 3.5))    = sqrt(11.375 / 10.5) = 1.040833
    expected = {
        RoughLevel.LOW: 1.040833,
        RoughLevel.GOOD_ENOUGH: 0.5,
        RoughLevel.MEDIUM: 0.763763,
        RoughLevel.HIGH: 1.040833,
    }
    assert {level: round(spread, 6) for level, spread in LEVEL_SPREADS.items()} == expected
    assert all(level_spread(level) == LEVEL_SPREADS[level] for level in RoughLevel)


@pytest.mark.parametrize(
    ("weight", "reply_text", "level"),
    [
        (8.0, "High", RoughLevel.HIGH),  # the button, as the API sends it
        (8.0, None, RoughLevel.HIGH),  # the button's level field alone
        (3.0, "good enough", RoughLevel.GOOD_ENOUGH),
        (8.0, "8", None),  # a typed number is exact, even when it is a level's weight
        (8.0, "8/10", None),
        (3.0, "3.0", None),  # the exact box sends decimals as typed, with exact_weight
        (8.0, "8.0", None),
        (7.5, "7.5", None),
        (7.0, None, None),  # not a level's weight: exact
        (7.0, "7", None),
    ],
)
def test_an_answer_is_rough_only_when_it_is_a_levels_weight_from_words(
    weight: float, reply_text: str | None, level: RoughLevel | None
) -> None:
    asked = answered("cpu_performance", weight, reply_text)
    assert rough_level(asked) == level
    expected = LEVEL_SPREADS[level] if level is not None else SETTINGS.exact_sd
    assert answer_sd(asked, SETTINGS) == expected


# --- the beliefs from the answers -----------------------------------------------------------------------------------


def test_before_any_answer_every_criterion_is_its_default_at_the_prior_sd() -> None:
    beliefs = beliefs_for(state(), SETTINGS)
    assert beliefs == {c.key: WeightBelief(mean=c.default_weight, sd=2.5) for c in ex.criteria().criteria}


def test_an_answer_moves_only_its_own_criterion() -> None:
    # cpu_performance: default 8, sd 2.5; "High" is 8 at the high spread 1.040833 (variance 1.083333)
    # precisions 0.16 + 0.923077 = 1.083077, variance 0.923295, sd 0.960883, mean 8 (prior and answer agree)
    # battery_life: default 7, "Low" is 2 at the low spread: mean = 0.923295 * (7 * 0.16 + 2 * 0.923077) = 2.738636
    history = (answered("cpu_performance", 8.0, "High"), answered("battery_life", 2.0, "Low"))
    beliefs = beliefs_for(state(*history), SETTINGS)
    assert beliefs["cpu_performance"].mean == pytest.approx(8.0)
    assert beliefs["battery_life"].mean == pytest.approx(2.738636, abs=1e-6)
    sure = math.sqrt(1 / (1 / 2.5**2 + 1 / LEVEL_SPREADS[RoughLevel.HIGH] ** 2))
    assert beliefs["cpu_performance"].sd == pytest.approx(sure)
    assert sure == pytest.approx(0.960883, abs=1e-6)
    moved = ("cpu_performance", "battery_life")
    others = {k: b for k, b in beliefs.items() if k not in moved}
    priors = {c.key: WeightBelief(mean=c.default_weight, sd=2.5) for c in ex.criteria().criteria if c.key not in moved}
    assert others == priors


def test_skipped_and_pending_questions_keep_the_prior() -> None:
    beliefs = beliefs_for(state(entry("cpu_performance", "skipped"), entry("battery_life", "pending")), SETTINGS)
    assert beliefs["cpu_performance"] == WeightBelief(mean=8.0, sd=2.5)
    assert beliefs["battery_life"] == WeightBelief(mean=7.0, sd=2.5)


def test_the_order_of_the_history_does_not_change_the_beliefs() -> None:
    a = answered("cpu_performance", 8.0, "High")
    b = answered("display_quality", 6.0, "6")
    assert beliefs_for(state(a, b), SETTINGS) == beliefs_for(state(b, a), SETTINGS)


def test_the_shoppers_rubric_edits_never_change_the_beliefs() -> None:
    plain = state(answered("cpu_performance", 8.0, "High"))
    edited = InterviewState.model_validate({**plain.model_dump(), "manual_weights": {"cpu_performance": 1.0}})
    assert beliefs_for(edited, SETTINGS) == beliefs_for(plain, SETTINGS)


# --- the settings ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "numbers",
    [
        {"prior_sd": 0.0, "exact_sd": 1.5, "settled_sd": 1.2},
        {"prior_sd": 2.5, "exact_sd": -1.0, "settled_sd": 1.2},
        {"prior_sd": 5.5, "exact_sd": 1.5, "settled_sd": 1.2},
        {"prior_sd": 2.5, "exact_sd": 1.5, "settled_sd": 2.5},  # nothing to ask: every prior would be settled already
    ],
)
def test_settings_out_of_range_are_refused(numbers: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        BeliefSettings(**numbers)


def test_the_default_settings_are_the_tuned_ones_and_valid() -> None:
    settings = BeliefSettings()
    assert 0 < settings.settled_sd < settings.prior_sd <= 5
    assert 0 < settings.exact_sd <= 5
    assert set(LEVEL_SPREADS) == set(ROUGH_LEVEL_WEIGHTS)
