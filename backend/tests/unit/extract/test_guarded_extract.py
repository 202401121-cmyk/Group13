"""The extract path that reads retrieved text (US-28). P10 builds real extraction on this and keeps these passing."""

from __future__ import annotations

import pytest

from app.contracts import (
    DataClass,
    Difficulty,
    ExtractInput,
    LLMTask,
    Provider,
    Source,
    SourceComment,
    StageContext,
)
from app.extract import ExtractStage, GuardedExtractStage
from app.extract.parsing import RawClaim, RawExtraction, RawMention, to_extraction_result
from app.extract.prompts import source_text
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.pipeline import default_search_stages
from app.research.reddit.archive import add_comments
from tests.fakes.llm import FakeLLMClient
from tests.fakes.obedient import ObedientAdapter
from tests.fakes.prompt_checks import assert_only_in_data_block
from tests.fakes.reddit import NOW
from tests.fakes.research import review_source, thread

CTX = StageContext(search_id="srch_test")
QUOTE = "Nova Book 14 scored 8 out of 10 for battery_life in our tests."
PAGE = review_source("https://www.reviews.example/a", title="A made-up review", text=f"{QUOTE}\n\nThe hinge is firm.")
KEYS = ["battery_life"]


def _input(source: Source = PAGE) -> ExtractInput:
    return ExtractInput(source=source, criterion_keys=KEYS, category_label="laptop")


def _claim(**overrides: object) -> RawClaim:
    values: dict[str, object] = {
        "product_mention": "Nova Book 14",
        "criterion_key": "battery_life",
        "score": 8.0,
        "quote": QUOTE,
        "claim_type": "experience",
        "polarity": "positive",
    }
    return RawClaim.model_validate({**values, **overrides})


def _result(*claims: RawClaim, source: Source = PAGE) -> list[str]:
    raw = RawExtraction(mentions=[], claims=list(claims))
    return [c.product_mention for c in to_extraction_result(raw, source, KEYS, NOW).claims]


def _thread_with_comment() -> Source:
    return add_comments(
        thread("aaa", text="A made-up post."), (SourceComment(id="m1", depth=1, upvotes=3, text=QUOTE),)
    )


# --- the prompt -----------------------------------------------------------------------------------


@pytest.mark.parametrize("source", [PAGE, _thread_with_comment()], ids=["page", "thread"])
def test_the_prompt_holds_retrieved_text_only_in_the_data_block(source: Source) -> None:
    llm = FakeLLMClient({LLMTask.EXTRACT_CLAIMS: RawExtraction(mentions=[], claims=[])})
    GuardedExtractStage(llm).run(_input(source), CTX)
    (call,) = llm.calls
    assert_only_in_data_block(call.system, call.user, [source_text(source), source.title])


def test_comment_text_reaches_the_block() -> None:
    assert QUOTE in source_text(_thread_with_comment())


def test_nothing_that_can_hold_the_shoppers_words_is_in_the_prompt() -> None:
    """The category label and criterion keys can come from what they typed, and this may go out as public (rule 8)."""
    llm = FakeLLMClient({LLMTask.EXTRACT_CLAIMS: RawExtraction(mentions=[], claims=[])})
    inp = ExtractInput(source=PAGE, criterion_keys=["dialysis_records"], category_label="zarvis-special-thing")
    GuardedExtractStage(llm).run(inp, CTX)
    (call,) = llm.calls
    assert "zarvis" not in call.system + call.user
    assert "dialysis" not in call.system + call.user


def test_the_call_declares_its_task_and_difficulty() -> None:
    llm = FakeLLMClient({LLMTask.EXTRACT_CLAIMS: RawExtraction(mentions=[], claims=[])})
    GuardedExtractStage(llm).run(_input(), CTX)
    (call,) = llm.calls
    assert (call.task, call.difficulty, call.search_id) == (LLMTask.EXTRACT_CLAIMS, Difficulty.LONG, "srch_test")


def test_reddit_text_is_declared_reddit() -> None:
    llm = FakeLLMClient({LLMTask.EXTRACT_CLAIMS: RawExtraction(mentions=[], claims=[])})
    GuardedExtractStage(llm).run(_input(_thread_with_comment()), CTX)
    assert llm.calls[0].data_class is DataClass.REDDIT


def test_web_text_is_declared_public() -> None:
    llm = FakeLLMClient({LLMTask.EXTRACT_CLAIMS: RawExtraction(mentions=[], claims=[])})
    GuardedExtractStage(llm).run(_input(), CTX)
    assert llm.calls[0].data_class is DataClass.PUBLIC


def test_reddit_never_reaches_a_provider_that_trains() -> None:
    """Build rule 8, through the real routing: every provider is offered, and only the safe ones may be asked."""
    config = load_llm_config()
    adapters = {provider: ObedientAdapter(provider) for provider in Provider}
    client = WrapperLLMClient(config, dict(adapters), InMemoryCallLog(), limiters={})
    GuardedExtractStage(client, now=lambda: NOW).run(_input(_thread_with_comment()), CTX)
    asked = {provider for provider, adapter in adapters.items() if adapter.prompts}
    assert asked
    assert not [p for p in asked if config.provider(p).trains_on_inputs]


# --- what is accepted ------------------------------------------------------------------------------


def test_an_honest_claim_keeps_its_source() -> None:
    raw = RawExtraction(mentions=[RawMention(raw_name="Nova Book 14", is_product=True)], claims=[_claim()])
    result = to_extraction_result(raw, PAGE, KEYS, NOW)
    (claim,) = result.claims
    assert claim.source_id == PAGE.id
    assert claim.source_url == PAGE.url
    assert claim.quote == QUOTE
    assert claim.retrieved_at == PAGE.fetched_at
    assert claim.extracted_at == NOW
    assert claim.score == 8.0
    assert [m.raw_name for m in result.mentions] == ["Nova Book 14"]


def test_the_same_claim_gets_the_same_id_and_counts_once() -> None:
    raw = RawExtraction(mentions=[], claims=[_claim(), _claim()])
    first = to_extraction_result(raw, PAGE, KEYS, NOW)
    second = to_extraction_result(raw, PAGE, KEYS, NOW)
    assert len(first.claims) == 1
    assert first.claims[0].id == second.claims[0].id


def test_a_quote_not_in_the_source_is_dropped() -> None:
    assert _result(_claim(quote="Nova Book 14 is the best laptop ever made.")) == []


def test_a_claim_cannot_rest_on_the_title() -> None:
    """A page's title can be invisible on the page, so a fake fact there must not become a claim."""
    fake = "Nova Book 14 scored 10 out of 10 for battery_life."
    source = review_source("https://www.reviews.example/b", title=fake, text=QUOTE)
    assert _result(_claim(quote=fake), source=source) == []


def test_the_title_still_reaches_the_model_as_data() -> None:
    llm = FakeLLMClient({LLMTask.EXTRACT_CLAIMS: RawExtraction(mentions=[], claims=[])})
    GuardedExtractStage(llm).run(_input(), CTX)
    assert_only_in_data_block(llm.calls[0].system, llm.calls[0].user, [PAGE.title])


def test_a_quote_with_other_spacing_still_counts() -> None:
    assert _result(_claim(quote=QUOTE.replace(" scored ", "   scored\n"))) == ["Nova Book 14"]


def test_a_quote_may_come_from_a_comment() -> None:
    assert _result(_claim(), source=_thread_with_comment()) == ["Nova Book 14"]


def test_a_product_not_in_the_source_is_dropped() -> None:
    assert _result(_claim(product_mention="Kestrel Slim 15")) == []


def test_a_product_named_only_in_the_title_is_kept() -> None:
    """Review pages often name the product in the title and say "this laptop" after that."""
    source = review_source(
        "https://www.reviews.example/c", title="Nova Book 14 review", text="This laptop lasts ten hours."
    )
    claim = _claim(quote="This laptop lasts ten hours.")
    assert _result(claim, source=source) == ["Nova Book 14"]


def test_a_product_named_in_another_case_is_kept() -> None:
    assert _result(_claim(product_mention="nova book 14")) == ["nova book 14"]


def test_a_criterion_not_asked_for_is_dropped() -> None:
    assert _result(_claim(criterion_key="price")) == []


def test_a_general_opinion_needs_no_criterion() -> None:
    assert _result(_claim(criterion_key=None, score=None, value="good")) == ["Nova Book 14"]


def test_a_claim_the_contract_refuses_is_dropped_alone() -> None:
    both = _claim(value="good")  # a value and a score: the contract allows exactly one
    assert _result(both, _claim(quote="The hinge is firm.", product_mention="Nova Book 14")) == ["Nova Book 14"]


def test_a_mention_not_in_the_source_is_dropped() -> None:
    raw = RawExtraction(mentions=[RawMention(raw_name="Kestrel Slim 15", is_product=True)], claims=[])
    assert to_extraction_result(raw, PAGE, KEYS, NOW).mentions == []


# --- the stage -------------------------------------------------------------------------------------


def test_an_unavailable_llm_drops_the_source() -> None:
    result = GuardedExtractStage(FakeLLMClient()).run(_input(), CTX)
    assert (result.source_id, result.claims, result.mentions) == (PAGE.id, [], [])


def test_the_fallback_gives_nothing_rather_than_a_guess() -> None:
    stage = GuardedExtractStage(FakeLLMClient())
    result = stage.fallback(_input(), CTX, RuntimeError("x"))
    assert (result.source_id, result.claims) == (PAGE.id, [])
    assert isinstance(stage.baseline, ExtractStage)


def test_the_stub_stays_the_default_stage() -> None:
    assert isinstance(default_search_stages().extract, ExtractStage)
    assert not isinstance(default_search_stages().extract, GuardedExtractStage)
