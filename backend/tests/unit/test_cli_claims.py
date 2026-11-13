"""python -m app.cli "..." --claims: per source, its URL, type, fetch time and claims (US-14 item 8)."""

from __future__ import annotations

import pytest

from app.cli import claims_block, main
from app.contracts import ExtractionResult, examples
from app.extract.recorder import RecordingStage
from app.extract.stage import ExtractStage

REQUEST = "best laptop for coding under 70k"


def test_the_claims_option_prints_url_type_time_and_claims(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([REQUEST, "--claims", "--no-db"]) == 0
    out = capsys.readouterr().out
    assert "Claims per source:" in out
    maker = next(s for s in examples.sources() if s.type.value == "manufacturer")
    assert f"manufacturer (manufacturer) via {maker.via}  {maker.fetched_at:%Y-%m-%d %H:%M:%S}Z  {maker.url}" in out
    assert '| battery_life | 10 | spec | "battery rated up to 10 hours"' in out
    assert '| experience | "' in out


def test_without_the_option_no_claims_are_printed(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([REQUEST, "--no-db"]) == 0
    assert "Claims per source:" not in capsys.readouterr().out


def test_a_score_is_shown_out_of_ten() -> None:
    (reddit, review, _) = examples.sources()
    claims = [c for c in examples.claims() if c.source_id == review.id and c.score is not None]
    block = claims_block([review], {review.id: ExtractionResult(source_id=review.id, claims=claims[:1])}, {})
    assert f"score {claims[0].score:g}/10" in block
    assert str(reddit.url) not in block


def test_a_value_shows_its_unit() -> None:
    (_, _, maker) = examples.sources()
    claim = next(c for c in examples.claims() if c.source_id == maker.id and c.criterion_key == "battery_life")
    claim = claim.model_validate({**claim.model_dump(), "unit": "hours"})
    block = claims_block([maker], {maker.id: ExtractionResult(source_id=maker.id, claims=[claim])}, {})
    assert "| 10 hours |" in block


def test_a_text_value_holding_its_unit_does_not_repeat_it() -> None:
    (_, _, maker) = examples.sources()
    claim = next(c for c in examples.claims() if c.source_id == maker.id and c.criterion_key == "battery_life")
    claim = claim.model_validate({**claim.model_dump(), "value": "13 hours 47 minutes", "unit": "hours"})
    block = claims_block([maker], {maker.id: ExtractionResult(source_id=maker.id, claims=[claim])}, {})
    assert "| 13 hours 47 minutes |" in block


def test_a_source_with_no_claims_says_so() -> None:
    (source, *_) = examples.sources()
    assert "No claims." in claims_block([source], {source.id: ExtractionResult(source_id=source.id)}, {})


def test_a_source_that_never_reported_says_so_too() -> None:
    (source, *_) = examples.sources()
    assert "No claims." in claims_block([source], {}, {})


def test_a_kind_of_product_is_marked() -> None:
    guide, claim = examples.product_guide(), examples.product_type_claim()
    block = claims_block([guide], {guide.id: ExtractionResult(source_id=guide.id, claims=[claim])}, {})
    assert "memory foam sleeves (kind of product) |" in block


def test_a_quote_with_a_line_break_prints_on_one_line() -> None:
    guide, claim = examples.product_guide(), examples.product_type_claim()
    claim = claim.model_validate({**claim.model_dump(), "quote": "Memory foam\nsleeves"})
    block = claims_block([guide], {guide.id: ExtractionResult(source_id=guide.id, claims=[claim])}, {})
    assert '"Memory foam sleeves"' in block


def test_the_recorder_passes_on_the_stages_coverage_note() -> None:
    """The runner asks the stage for its note, and --claims must not lose it."""
    from app.contracts import ExtractInput, StageContext
    from app.extract import GuardedExtractStage
    from app.extract.settings import ExtractSettings
    from tests.fakes.llm import FakeLLMClient

    recorder = RecordingStage(GuardedExtractStage(FakeLLMClient(), ExtractSettings()))
    inp = ExtractInput(source=examples.sources()[0], criterion_keys=["battery_life"], category_label="laptop")
    recorder.run(inp, StageContext(search_id="srch_a"))
    assert "1 source could not be read" in (recorder.coverage_note("srch_a") or "")


def test_the_recorder_keeps_each_sources_result_and_stays_transparent() -> None:
    inner = ExtractStage()
    recorder = RecordingStage(inner)
    source = examples.sources()[0]
    assert recorder.is_placeholder is True
    ctx_error = RuntimeError("made up")
    from app.contracts import ExtractInput, StageContext

    inp = ExtractInput(source=source, criterion_keys=["battery_life"], category_label="laptop")
    ctx = StageContext(search_id="srch_a")
    out = recorder.run(inp, ctx)
    assert recorder.results[source.id] is out
    assert recorder.fallback(inp, ctx, ctx_error).claims == []
    with pytest.raises(AttributeError):
        _ = recorder.no_such_thing  # type: ignore[attr-defined]


def test_the_match_block_shows_the_names_before_and_the_products_after() -> None:
    """US-15: --claims shows what match was given and what it made of it."""
    from app.cli import match_block
    from app.contracts import MatchInput, ProductMention, StageContext
    from app.entities import FuzzyMatchStage

    names = ["Sony WH-1000XM5", "XM5", "XM5", "iPhone 16 128GB", "iPhone 16 256GB"]
    inp = MatchInput(mentions=[ProductMention(raw_name=n, is_product=True) for n in names])
    matched = FuzzyMatchStage().run(inp, StageContext(search_id="srch_aaaaaaaaaaaaaaaaaaaaaaaa"))
    text = match_block(names, matched)
    assert "Names before matching (5 mentions, 4 different):" in text
    assert "  XM5  (x2)" in text
    assert "Products after matching (2, method fuzzy):" in text
    assert "    also: XM5" in text
    assert "    variant: 128 GB" in text and "    variant: 256 GB" in text
    assert "The search stopped before match." in match_block([], None)


def test_each_source_says_how_it_was_read() -> None:
    """direct, jina, firecrawl, api or search_snippet (US-38), so a live run shows which pages needed a reader."""
    (_, review, _) = examples.sources()
    block = claims_block([review], {}, {})
    assert f"via {review.via}" in block
