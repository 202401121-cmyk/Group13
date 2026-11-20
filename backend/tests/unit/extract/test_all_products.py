"""US-49 follow-up (20 Nov 2026): extraction asks for every product a source names, as bare names, so a list page's
products all fit the answer. Every product, page and reply here is made up."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.extract.parsing import RawExtraction
from app.extract.prompts import MENTIONS_TOKENS, SYSTEM_PROMPT


def test_the_prompt_asks_for_every_product_named_as_bare_names() -> None:
    assert "every product the source names, up to 60, just its name" in SYSTEM_PROMPT
    assert "at most 15" not in SYSTEM_PROMPT
    assert "If it names no product and says nothing useful, return empty lists." in SYSTEM_PROMPT


def test_a_bare_name_is_a_product_someone_can_buy() -> None:
    raw = RawExtraction.model_validate({"mentions": ["Nova Book 14", "Kite X2"], "claims": []})
    assert [(m.raw_name, m.is_product) for m in raw.mentions] == [("Nova Book 14", True), ("Kite X2", True)]


def test_the_object_form_still_reads_and_marks_a_kind_of_product() -> None:
    raw = RawExtraction.model_validate(
        {"mentions": ["Nova Book 14", {"raw_name": "memory foam", "is_product": False}], "claims": []}
    )
    assert [(m.raw_name, m.is_product) for m in raw.mentions] == [("Nova Book 14", True), ("memory foam", False)]


@pytest.mark.parametrize("bad", [[7], [None], [["Nova Book 14"]], [{"is_product": True}]])
def test_anything_else_is_still_refused(bad: list[object]) -> None:
    with pytest.raises(ValidationError):
        RawExtraction.model_validate({"mentions": bad, "claims": []})


def test_sixty_bare_names_fit_the_same_mention_budget() -> None:
    from app.llm.fitting import estimate_tokens

    names = ", ".join(f'"Made Up Product {n} Pro"' for n in range(60))
    assert estimate_tokens(f'"mentions": [{names}]') <= MENTIONS_TOKENS
