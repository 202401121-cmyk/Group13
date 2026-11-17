"""The S4-2 smartphone brief stopped at intake with a question (US-02, S4-29). It names a phone, and lists features
("a good camera", "8 GB RAM"), so it is a smartphone: the LLM calling it ambiguous must not win over the shopper's own
word. A word that could mean different products still asks. Made-up requests.
"""

from __future__ import annotations

from app.intake.parsing import RawIntent, to_intent
from app.intake.rules import detect_by_rules

PHONE = "phone with a good camera under ₹30,000 with at least 8 GB RAM"


def test_a_phone_with_a_good_camera_and_ram_is_a_smartphone() -> None:
    by_rules = detect_by_rules(PHONE, PHONE)
    assert (by_rules.category, by_rules.ready_for_research) == ("smartphone", True)

    for raw in (
        RawIntent(category="phone", ambiguous=True, options=["Smartphone", "Camera"]),  # S4-2's likely answer
        RawIntent(category=None, ambiguous=True),
        RawIntent(category="camera", ambiguous=True, options=["Smartphone", "Camera"]),  # unsure, a feature
        RawIntent(category="smartphone", ambiguous=False),
    ):
        intent = to_intent(raw, PHONE, PHONE)
        assert intent.category == "smartphone"
        assert intent.ready_for_research
        assert not intent.needs_clarification
        assert intent.budget is not None and intent.budget.max is not None
        assert intent.budget.max.amount == 30_000


def test_the_llms_ambiguity_still_asks_when_no_category_is_named() -> None:
    watch = "a good watch under 5000"
    asked = to_intent(RawIntent(category="watch", ambiguous=True), watch, watch)
    assert asked.needs_clarification and not asked.ready_for_research

    bat = "a bat for my son"
    raw = RawIntent(category="bat", ambiguous=True, options=["Cricket bat", "Baseball bat"])
    asked = to_intent(raw, bat, bat)
    assert asked.needs_clarification and asked.clarification_options == ["Cricket bat", "Baseball bat"]
