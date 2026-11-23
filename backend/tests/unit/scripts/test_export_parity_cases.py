"""US-23 (S5-5 DECIDED 2): the parity cases the browser's re-rank is checked against. They come from the real decide
code, the committed file is exactly what the script writes now, and the cases are what they say they are."""

from __future__ import annotations

import json
import sys

import pytest

from app.contracts import DecideInput
from app.decide import decide
from scripts import export_parity_cases as parity

pytestmark = pytest.mark.skipif(sys.version_info >= (3, 12), reason="written with CI's Python 3.11 (sum() changed)")


@pytest.fixture(scope="module")
def document() -> dict:  # type: ignore[type-arg]
    return parity.document()


def test_the_committed_file_is_what_the_script_writes_now() -> None:
    """CI also runs the script and fails on any difference: change decide, and the browser's cases change with it."""
    assert parity.OUT.read_text(encoding="utf-8") == parity.render()


def test_thirty_cases_from_the_five_briefs_and_the_five_recorded_runs(document: dict) -> None:  # type: ignore[type-arg]
    cases = document["cases"]
    assert len(cases) == 30
    inputs = {c["id"].rsplit(" #", 1)[0] for c in cases}
    assert len([i for i in inputs if i.startswith("brief ")]) == 5
    assert len([i for i in inputs if i.startswith("recorded gate_")]) == 5
    assert sum(1 for c in cases if sum(e["score"] is not None for e in c["expected"]) >= 2) >= 15


def test_weights_are_slider_values_and_every_product_is_scored_by_the_real_decide(document: dict) -> None:  # type: ignore[type-arg]
    for case in document["cases"]:
        assert all(0 <= w["weight"] <= 10 and (w["weight"] * 2).is_integer() for w in case["weights"])
        # the same shown products, re-ranked: nothing added, nothing dropped
        assert sorted(p["product_id"] for p in case["products"]) == sorted(e["product_id"] for e in case["expected"])


def test_the_expected_ranking_is_decides_weighted_ranking_with_those_weights() -> None:
    name, inp = next(parity.brief_inputs())
    rubric = parity.reweighted(inp.rubric, parity.random.Random(parity.SEED * 1000))
    result = decide(DecideInput.model_validate({**inp.model_dump(), "rubric": rubric.model_dump()}), weighted_only=True)
    (first, *_) = parity.cases([(name, inp)])
    assert first["expected"] == [
        {"product_id": r.product_id, "score": r.score, "coverage": r.coverage} for r in result.ranked
    ]
    assert json.loads(json.dumps(first)) == first  # every number survives JSON exactly, so the browser reads the same
