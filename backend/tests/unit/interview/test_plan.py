"""The interview plan (US-05): default order, the question cap, topic rules and the budget rule."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from app.contracts import AskedQuestion, Budget, CriteriaSet, Criterion, Intent, Money, ReplyClassification, ReplyType
from app.contracts import examples as ex
from app.interview.plan import (
    DEFAULT_RULES_PATH,
    InterviewConfigError,
    default_order,
    load_interview_rules,
    mentions,
    plan_questions,
)
from app.interview.questions import in_sentence, question_for, question_id


def crit(key: str, weight: float, label: str | None = None, vfm: bool = False) -> Criterion:
    return Criterion(
        key=key,
        label=label or key.replace("_", " ").capitalize(),
        definition=f"How good the product is on {key.replace('_', ' ')}, which matters for daily use.",
        default_weight=weight,
        is_value_for_money=vfm,
    )


PHONE = CriteriaSet(
    category="smartphone",
    source="generated",
    criteria=[
        crit("camera_quality", 7),
        crit("battery_life", 8),
        crit("charging_speed", 5),
        crit("display_quality", 6),
        crit("wireless_charging", 3),
        crit("premium_build", 4),
        crit("value_for_money", 6, vfm=True),
    ],
)


def intent(category: str = "smartphone", budget_max: float | None = None, currency: str = "INR") -> Intent:
    budget = Budget(max=Money(amount=budget_max, currency=currency)) if budget_max is not None else None
    return Intent(raw_query="x", category=category, category_label="X", budget=budget, detected_by="rules")


def answered(criterion: Criterion, weight: float) -> AskedQuestion:
    reply = ReplyClassification(reply_type=ReplyType.ANSWER, weight=weight, classified_by="fallback")
    return AskedQuestion(question=question_for(criterion), status="answered", classification=reply)


def by_key(key: str) -> Criterion:
    return next(c for c in PHONE.criteria if c.key == key)


def test_default_order_is_by_default_weight_with_ties_in_list_order() -> None:
    keys = [c.key for c in default_order(PHONE)]
    assert keys == [
        "battery_life",
        "camera_quality",
        "display_quality",
        "value_for_money",
        "charging_speed",
        "premium_build",
        "wireless_charging",
    ]
    plan = plan_questions(PHONE, intent(), [])
    assert plan.to_ask == tuple(keys)
    assert plan.next_key == "battery_life"


def test_at_most_max_questions_are_planned() -> None:
    assert plan_questions(PHONE, intent(), [], max_questions=3).to_ask == (
        "battery_life",
        "camera_quality",
        "display_quality",
    )


def test_answered_and_skipped_questions_leave_the_plan() -> None:
    skipped = AskedQuestion(question=question_for(by_key("camera_quality")), status="skipped")
    plan = plan_questions(PHONE, intent(), [answered(by_key("battery_life"), 8), skipped])
    assert plan.next_key == "display_quality"


def test_a_low_answer_removes_the_rest_of_its_topic() -> None:
    plan = plan_questions(PHONE, intent(), [answered(by_key("battery_life"), 2)])
    # battery_life and charging_speed and wireless_charging share the [battery, charging] topic
    assert "charging_speed" not in plan.to_ask and "wireless_charging" not in plan.to_ask
    assert dict(plan.inferred) == {"charging_speed": 2.0, "wireless_charging": 2.0}
    assert plan.next_key == "camera_quality"


def test_changing_the_low_answer_back_to_high_brings_the_questions_back() -> None:
    low = plan_questions(PHONE, intent(), [answered(by_key("battery_life"), 2)])
    high = plan_questions(PHONE, intent(), [answered(by_key("battery_life"), 8)])
    assert "charging_speed" not in low.to_ask
    assert "charging_speed" in high.to_ask and "wireless_charging" in high.to_ask
    assert high.inferred == {}


def test_a_medium_answer_settles_nothing() -> None:
    assert plan_questions(PHONE, intent(), [answered(by_key("battery_life"), 5)]).inferred == {}


def test_already_answered_questions_are_never_overwritten_by_a_topic() -> None:
    history = [answered(by_key("charging_speed"), 9), answered(by_key("battery_life"), 1)]
    plan = plan_questions(PHONE, intent(), history)
    assert "charging_speed" not in plan.inferred


def test_budget_below_the_threshold_skips_premium_questions() -> None:
    plan = plan_questions(PHONE, intent(budget_max=25_000), [])
    assert plan.premium_skipped == {"wireless_charging", "premium_build"}
    assert "premium_build" not in plan.to_ask and "wireless_charging" not in plan.to_ask


@pytest.mark.parametrize(
    ("budget", "currency", "category"),
    [(40_000, "INR", "smartphone"), (None, "INR", "smartphone"), (25_000, "USD", "smartphone"), (100, "INR", "tablet")],
)
def test_premium_questions_stay_otherwise(budget: float | None, currency: str, category: str) -> None:
    criteria = CriteriaSet.model_validate({**PHONE.model_dump(), "category": category})
    plan = plan_questions(criteria, intent(category, budget, currency), [])
    assert plan.premium_skipped == frozenset()
    assert "premium_build" in plan.to_ask


def test_topics_match_whole_words_only() -> None:
    support = crit("ergonomic_support", 9, "Ergonomic support")
    service = crit("after_sales_service", 4, "After-sales service")
    assert not mentions("support", crit("supported_formats", 5))
    assert mentions("low_light", crit("night_shots", 5, "Low-light performance"))
    assert mentions("after_sales", service) and not mentions("after_sales", support)
    chairs = CriteriaSet(
        category="office_chair",
        source="generated",
        criteria=[support, service, *(crit(f"other_{i}", 5) for i in range(4)), crit("value_for_money", 6, vfm=True)],
    )
    plan = plan_questions(chairs, intent("office_chair"), [answered(service, 0)])
    assert "ergonomic_support" not in plan.inferred


def test_the_real_rules_load() -> None:
    rules = load_interview_rules()
    assert rules.low_answer_weight == 2
    assert rules.budget_thresholds_inr["smartphone"] == 30_000
    assert ["battery", "charging"] in rules.topic_groups
    assert "wireless_charging" in rules.premium_words


def _write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "interview_rules.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _raw() -> dict[str, object]:
    data: dict[str, object] = yaml.safe_load(DEFAULT_RULES_PATH.read_text(encoding="utf-8"))
    return data


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"low_answer_weight": 11}, "less than or equal to 10"),
        ({"topic_groups": [["Battery Life"]]}, "pattern"),
        ({"topic_groups": [[]]}, "at least one word"),
        ({"budget_thresholds_inr": {"spaceship": 100}}, "spaceship"),
        ({"budget_thresholds_inr": {"laptop": 0}}, "above zero"),
        ({"surprise": 1}, "Extra inputs"),
    ],
)
def test_a_bad_rules_file_fails_clearly(tmp_path: Path, change: dict[str, object], message: str) -> None:
    with pytest.raises(InterviewConfigError, match=message):
        load_interview_rules(_write(tmp_path, {**_raw(), **change}))


def test_a_missing_rules_file_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(InterviewConfigError, match="cannot read"):
        load_interview_rules(tmp_path / "nope.yaml")


# --- question text -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("Cooking results", "How much do you care about cooking results?"),
        ("Battery life", "How much do you care about battery life?"),
        ("RAM and storage", "How much do you care about RAM and storage?"),
        ("GPS accuracy", "How much do you care about GPS accuracy?"),
        ("Smart TV software", "How much do you care about smart TV software?"),
    ],
)
def test_question_wording(label: str, text: str) -> None:
    assert question_for(crit("x_criterion", 5, label)).text == text


def test_question_id_reasons_and_buttons() -> None:
    criterion = ex.criteria().criteria[1]
    question = question_for(criterion)
    assert question.id == "q_battery_life"
    assert question.criterion_key == "battery_life"
    assert question.why_asking == criterion.definition
    assert question.quick_replies == ["Low", "Medium", "High", "Skip"]
    assert question_id("very-long-key-" + "x" * 60) == "q_very_long_key_" + "x" * 26
    long_definition = crit("x_criterion", 5).model_dump() | {
        "definition": "word " * 55
    }  # 275 chars: valid, but over 200
    assert len(question_for(Criterion.model_validate(long_definition)).why_asking or "") <= 200
    assert in_sentence("Wi-Fi range") == "wi-fi range"
