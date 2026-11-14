"""US-40 through the search: planted product names never reach the results, and the verify report counts the drops.

The run is tests/fixtures/verify/planted_names.json, a made-up recorded-style run with three planted names that no
source holds. Research, extract and match replay it; verify and decide are the real stages. Also S3-9's feature
exclusions: the search asks extract and verify for them as fixed keys. Every product, site and quote here is made up.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

import app.contracts as C
from app.contracts import examples as ex
from app.decide import WeightedDecideStage
from app.extract import GuardedExtractStage
from app.extract.settings import ExtractSettings
from app.pipeline import (
    MAX_VERIFY_KEYS,
    InMemoryResultStore,
    PipelineRunner,
    SearchStages,
    default_search_stages,
    without_products,
)
from app.verify import TrustVerifyStage
from tests.fakes.llm import FakeLLMClient
from tests.unit.test_sample_label import FakeRealStage

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "verify" / "planted_names.json"
RUN: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
PLANTED: list[str] = RUN["planted"]
REAL = sorted(RUN["named_by"])


class _Research:
    name = C.StageName.RESEARCH
    is_placeholder = False

    def run(self, inp: C.ResearchInput, ctx: C.StageContext) -> C.ResearchResult:
        return C.ResearchResult(queries=["a made-up query"], sources=RUN["sources"])

    def fallback(self, inp: C.ResearchInput, ctx: C.StageContext, error: Exception) -> C.ResearchResult:
        return C.ResearchResult(queries=["a made-up query"], sources=[])


class _Extract:
    """Each source's recorded claims and mentions. It remembers what the search asked it for (US-14)."""

    name = C.StageName.EXTRACT
    is_placeholder = False

    def __init__(self) -> None:
        self.asked: list[tuple[str, tuple[str, ...], bool, tuple[str, ...]]] = []

    def ask_for_specs(
        self, search_id: str, attributes: Sequence[str], has_budget: bool, features: Sequence[str] = ()
    ) -> None:
        self.asked.append((search_id, tuple(attributes), has_budget, tuple(features)))

    def run(self, inp: C.ExtractInput, ctx: C.StageContext) -> C.ExtractionResult:
        claims = [c for c in RUN["claims"] if c["source_id"] == inp.source.id]
        return C.ExtractionResult(source_id=inp.source.id, claims=claims, mentions=RUN["mentions"][inp.source.id])

    def fallback(self, inp: C.ExtractInput, ctx: C.StageContext, error: Exception) -> C.ExtractionResult:
        return C.ExtractionResult(source_id=inp.source.id)


class _OldExtract(_Extract):
    """A stage written before features were asked for: its ask_for_specs takes three arguments."""

    def ask_for_specs(self, search_id: str, attributes: Sequence[str], has_budget: bool) -> None:  # type: ignore[override]
        self.asked.append((search_id, tuple(attributes), has_budget, ()))


class _Match:
    name = C.StageName.MATCH
    is_placeholder = False

    def run(self, inp: C.MatchInput, ctx: C.StageContext) -> C.MatchResult:
        return C.MatchResult.model_validate(RUN["match"])

    def fallback(self, inp: C.MatchInput, ctx: C.StageContext, error: Exception) -> C.MatchResult:
        return C.MatchResult.model_validate(RUN["match"])


class _Verify(TrustVerifyStage):
    """The real verify, remembering its input and the filter keys it was given."""

    def __init__(self) -> None:
        super().__init__()
        self.inputs: list[C.VerifyInput] = []
        self.filters: list[frozenset[str]] = []

    def use_filter_keys(self, search_id: str, keys: Any) -> None:
        super().use_filter_keys(search_id, keys)
        self.filters.append(self.filter_keys(search_id))

    def run(self, inp: C.VerifyInput, ctx: C.StageContext) -> C.VerifyResult:
        self.inputs.append(inp)
        return super().run(inp, ctx)


def _stages(extract: _Extract | None = None, verify: _Verify | None = None) -> SearchStages:
    stages = default_search_stages()
    stages.research = _Research()
    stages.extract = extract or _Extract()
    stages.match = _Match()
    stages.verify = verify or _Verify()
    stages.enrich = FakeRealStage(stages.enrich)
    stages.explain = FakeRealStage(stages.explain)
    stages.commerce = FakeRealStage(stages.commerce)
    return stages


def _rubric(*exclusions: C.Exclusion) -> C.Rubric:
    base = ex.rubric()
    every = [*base.exclusions, *exclusions]
    return C.Rubric.model_validate({**base.model_dump(), "exclusions": [e.model_dump() for e in every]})


def _run(stages: SearchStages, rubric: C.Rubric | None = None) -> C.SearchResult:
    request = C.SearchRequest(session_id=ex.SESSION_ID, raw_query="a made-up request", rubric=rubric or ex.rubric())
    return PipelineRunner(stages, InMemoryResultStore()).run(request)


def _ids(names: Sequence[str]) -> set[str]:
    return {p["id"] for p in RUN["match"]["products"] if p["name"] in names}


def test_the_fixture_plants_three_names_no_source_holds() -> None:
    assert PLANTED == ["Novalux Air 7", "Quorra Book X3", "Zentrix Pro 9"]
    texts = " ".join(
        " ".join([s["title"], s["text"], *(c["text"] for c in s["comments"])]) for s in RUN["sources"]
    ).casefold()
    assert all(name.casefold() not in texts for name in PLANTED)
    assert {c["product_mention"] for c in RUN["claims"]} >= set(PLANTED)  # the model "read" claims about each


def test_the_planted_names_never_reach_the_results() -> None:
    result = _run(_stages())
    assert result.decision is not None and result.decision.method == "weighted"
    planted = _ids(PLANTED)
    shown = {p.product.id for p in result.products}
    assert shown == _ids(REAL)  # every real one, named in a title, a comment, by alias or in a quote only, stays
    assert not planted & {r.product_id for r in result.decision.ranked}
    assert not planted & {f.product_id for f in result.decision.filtered}
    dumped = result.model_dump_json()
    assert all(name not in dumped for name in PLANTED)
    assert all(product_id not in dumped for product_id in planted)  # nor their claims among the citations


def test_the_verify_report_counts_the_drops() -> None:
    result = _run(_stages())
    verify = next(r for r in result.stage_reports if r.stage is C.StageName.VERIFY)
    assert verify.status is C.StageStatus.OK
    assert "dropped 3 products no source names" in verify.notes


def test_a_verify_stage_without_notes_leaves_the_report_alone() -> None:
    stages = _stages()
    stages.verify = FakeRealStage(TrustVerifyStage())  # has no report_notes
    result = _run(stages)
    verify = next(r for r in result.stage_reports if r.stage is C.StageName.VERIFY)
    assert verify.notes == []


def test_excluded_features_reach_the_extract_ask_and_the_verify_keys() -> None:
    extract, verify = _Extract(), _Verify()
    rubric = _rubric(
        C.Exclusion(kind="feature", value="touchscreen"),
        C.Exclusion(kind="feature", value="glitter finish"),  # not in config/exclusions.yaml: never sent
        C.Exclusion(kind="feature", value="rgb lighting"),
    )
    _run(_stages(extract, verify), rubric)
    ((_, attributes, has_budget, features),) = extract.asked
    assert (attributes, has_budget, features) == (("ram_gb",), True, ("touchscreen", "rgb lighting"))
    (inp,) = verify.inputs
    weights = [w.key for w in rubric.weights]
    assert inp.criterion_keys == [*weights, "price_inr", "ram_gb", "has_touchscreen", "has_rgb_lighting"]
    (filters,) = verify.filters
    assert filters == {"price_inr", "ram_gb", "has_touchscreen", "has_rgb_lighting"}  # community words can't settle


def test_a_feature_written_another_way_is_asked_for_by_its_own_name() -> None:
    extract, verify = _Extract(), _Verify()
    _run(_stages(extract, verify), _rubric(C.Exclusion(kind="feature", value="Touch Screen")))
    assert extract.asked[0][3] == ("touchscreen",)
    assert verify.inputs[0].criterion_keys[-1] == "has_touchscreen"


def test_a_search_with_no_feature_exclusions_asks_for_none() -> None:
    extract, verify = _Extract(), _Verify()
    _run(_stages(extract, verify))
    assert extract.asked[0][3] == ()
    assert not any(k.startswith("has_") for k in verify.inputs[0].criterion_keys)


def test_an_extract_stage_without_features_still_gets_its_ask() -> None:
    extract = _OldExtract()
    result = _run(_stages(extract), _rubric(C.Exclusion(kind="feature", value="touchscreen")))
    assert [a[1:3] for a in extract.asked] == [(("ram_gb",), True)]
    assert result.decision is not None


def test_feature_keys_stay_within_the_verify_limit() -> None:
    verify = _Verify()
    names = ["touchscreen", "notch", "glossy screen", "bloatware", "rgb lighting", "fan noise", "refurbished"]
    weights = [
        {"key": f"made_up_criterion_{n}", "weight": 5.0, "normalized": 0.1, "origin": "default"} for n in range(10)
    ]
    rules = [
        {"attribute": f"made_up_spec_{n}", "operator": ">=", "value": 1.0, "source_text": "a made-up rule"}
        for n in range(16)
    ]
    rubric = C.Rubric.model_validate(
        {
            **_rubric(*(C.Exclusion(kind="feature", value=n) for n in names)).model_dump(),
            "weights": weights,
            "hard_rules": rules,
        }
    )
    _run(_stages(verify=verify), rubric)
    keys = verify.inputs[0].criterion_keys
    assert len(keys) == MAX_VERIFY_KEYS
    assert keys[-3:] == ["has_touchscreen", "has_notch", "has_glossy_screen"]  # after the rules, as room allows


class _BrokenNotes(TrustVerifyStage):
    def report_notes(self, search_id: str) -> list[str]:
        raise RuntimeError("made-up failure with secret text")


def test_broken_report_notes_never_stop_the_search(caplog: Any) -> None:
    stages = _stages()
    stages.verify = _BrokenNotes()
    result = _run(stages)
    verify = next(r for r in result.stage_reports if r.stage is C.StageName.VERIFY)
    assert verify.status is C.StageStatus.OK and verify.notes == []
    assert result.decision is not None
    assert all(name not in result.model_dump_json() for name in PLANTED)  # the drops still hold
    assert "secret text" not in caplog.text


# --- a feature written another way still rules a product out (review item 1) ----------------------------------------


class _ReadsFeatures(_Extract):
    """The recorded run, plus the review page saying whether Nova Book 14 has each feature asked for: yes."""

    def run(self, inp: C.ExtractInput, ctx: C.StageContext) -> C.ExtractionResult:
        out = super().run(inp, ctx)
        review = RUN["sources"][0]
        if inp.source.id != review["id"]:
            return out
        asked = [features for search_id, _, _, features in self.asked if search_id == ctx.search_id][-1]
        extra = [
            C.Claim(
                id=f"clm_feed{n:08x}",
                source_id=review["id"],
                source_url=review["url"],
                product_mention="Nova Book 14",
                criterion_key=key,
                value="yes",
                quote="Nova Book 14 has the best keyboard we tried",
                claim_type=C.ClaimType.SPEC,
                polarity="neutral",
                retrieved_at=out.claims[0].retrieved_at,
                extracted_at=out.claims[0].extracted_at,
            )
            for n, key in enumerate(f"has_{name.replace(' ', '_')}" for name in asked)
        ]
        return C.ExtractionResult.model_validate({**out.model_dump(), "claims": [*out.model_dump()["claims"], *extra]})


@pytest.mark.parametrize("written", ["touch screen", "rgb"])
def test_a_feature_written_another_way_rules_the_product_out_end_to_end(written: str) -> None:
    result = _run(_stages(_ReadsFeatures()), _rubric(C.Exclusion(kind="feature", value=written)))
    assert result.decision is not None
    nova = next(p["id"] for p in RUN["match"]["products"] if p["name"] == "Nova Book 14")
    held = {f.product_id: f for f in result.decision.filtered}
    assert nova in held, "verify's Known yes was read, so Nova Book 14 is ruled out"
    assert (held[nova].group, held[nova].reasons, held[nova].rule_attribute) == (
        "filtered_out",
        [f"You ruled out {written}"],
        "feature",
    )


# --- a dropped product never stays in the decision (review item 5) ---------------------------------------------------


class _DecideFails(WeightedDecideStage):
    def run(self, inp: C.DecideInput, ctx: C.StageContext) -> C.DecideResult:
        raise RuntimeError("made-up failure")


def test_the_decide_stubs_fallback_never_lists_a_dropped_product() -> None:
    """The example sources name neither Aurora Air 13 nor Vela Pro 14 (nor the product type "thin-and-light laptops"),
    so verify drops them; the decide stub's sample decision lists Aurora and Vela as filtered, and the result must not
    show them with no product."""
    stages = default_search_stages()  # tests/conftest.py gives the research and extract stubs: the example run
    stages.decide = _DecideFails()
    result = _run(stages)
    dropped = {"prd_aurora_air_13", "prd_vela_pro_14", "prd_thin_and_light_laptops"}
    verify = next(r for r in result.stage_reports if r.stage is C.StageName.VERIFY)
    assert "dropped 3 products no source names" in verify.notes
    assert result.decision is not None
    assert dropped.isdisjoint({r.product_id for r in result.decision.ranked})
    assert dropped.isdisjoint({f.product_id for f in result.decision.filtered})
    shown = {p.product.id for p in result.products}
    assert dropped.isdisjoint(shown)
    assert {r.product_id for r in result.decision.ranked} <= shown
    assert C.SearchResult.model_validate_json(result.model_dump_json()) == result


def test_a_dropped_product_ranked_by_decide_is_removed_and_the_ranks_renumbered() -> None:
    planted = "prd_zentrix_pro_9"
    stub = ex.decision()
    swapped = json.loads(stub.model_dump_json().replace("prd_nova_book_14", planted))
    decision = C.DecideResult.model_validate(swapped)  # the planted product first, best match, tied with Kestrel
    out = without_products(decision, {planted})
    assert [(r.product_id, r.rank, r.label) for r in out.ranked] == [
        ("prd_kestrel_slim_15", 1, "best_match"),
        ("prd_orion_play_15", 2, "runner_up"),
    ]
    assert out.ranked[0].tied_with == []
    assert out.ranked[0].gap_to_leader == 0.0
    assert out.ranked[1].gap_to_leader == round(out.ranked[0].score - out.ranked[1].score, 2)  # type: ignore[operator]
    assert out.filtered == stub.filtered
    assert C.DecideResult.model_validate(out.model_dump()) == out


def test_a_decision_whose_every_shown_product_was_dropped_still_validates() -> None:
    stub = ex.decision()
    out = without_products(stub, {r.product_id for r in stub.ranked} | {"prd_vela_pro_14"})
    assert out.ranked == []
    assert [f.product_id for f in out.filtered] == ["prd_aurora_air_13"]
    assert out.relaxation_hint  # nothing passes, so the contract asks for a hint


def test_a_decision_with_nothing_dropped_is_unchanged() -> None:
    stub = ex.decision()
    assert without_products(stub, set()) is stub
    assert without_products(stub, {"prd_not_in_it"}) is stub


# --- the extract stage keeps only features it knows (defence in depth) ---------------------------------------------


def test_the_extract_stage_keeps_only_known_features_by_their_own_names() -> None:
    stage = GuardedExtractStage(FakeLLMClient({}), ExtractSettings())
    stage.ask_for_specs("srch_features01", ["ram_gb"], True, features=["my daughters glitter case", "Touch Screen"])
    ask = stage._ask_for("srch_features01")
    assert ask is not None and ask.features == ("touchscreen",)
