"""Reading one source (US-14, US-28): the prompt, what code accepts from the answer, and the stage."""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest

from app.contracts import (
    ClaimType,
    DataClass,
    Difficulty,
    ExtractInput,
    LLMTask,
    Provider,
    Source,
    SourceComment,
    StageContext,
    StageName,
    StageReport,
    StageStatus,
)
from app.criteria.fallback import fallback_criteria
from app.extract import ExtractStage, GuardedExtractStage
from app.extract.parsing import RawClaim, RawExtraction, RawMention, to_extraction_result
from app.extract.prompts import MAX_TITLE_CHARS, allowed_criteria, source_text
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.llm.data_block import outside_data
from app.llm.errors import ProviderError
from app.pipeline import made_by_placeholder
from app.research.reddit.archive import add_comments
from tests.fakes.llm import FakeAdapter, FakeLLMClient
from tests.fakes.obedient import ObedientAdapter
from tests.fakes.prompt_checks import assert_only_in_data_block
from tests.fakes.reddit import NOW
from tests.fakes.research import review_source, thread

CTX = StageContext(search_id="srch_test")
QUOTE = "Nova Book 14 scored 8 out of 10 for battery_life in our tests."
PAGE = review_source("https://www.reviews.example/a", title="A made-up review", text=f"{QUOTE}\n\nThe hinge is firm.")
KEYS = ["battery_life"]
SHOPPER_KEY = "dialysis_records"  # made from what a shopper typed: it must never reach a provider that trains


def _input(source: Source = PAGE, keys: list[str] | None = None, category: str = "laptop") -> ExtractInput:
    return ExtractInput(source=source, criterion_keys=keys or KEYS, category_label=category)


def _empty_llm() -> FakeLLMClient:
    return FakeLLMClient({LLMTask.EXTRACT_CLAIMS: RawExtraction(mentions=[], claims=[])})


def _claim(**overrides: object) -> RawClaim:
    values: dict[str, object] = {
        "product_mention": "Nova Book 14",
        "criterion_key": "battery_life",
        "score": 8.0,
        "quote": QUOTE,
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
    llm = _empty_llm()
    GuardedExtractStage(llm).run(_input(source), CTX)
    (call,) = llm.calls
    assert_only_in_data_block(call.system, call.user, [source_text(source), source.title])


def test_comment_text_reaches_the_block() -> None:
    assert QUOTE in source_text(_thread_with_comment())


def test_each_source_gets_its_own_call() -> None:
    llm = _empty_llm()
    other = review_source("https://www.reviews.example/b", text="Another made-up page.")
    stage = GuardedExtractStage(llm)
    stage.run(_input(PAGE), CTX)
    stage.run(_input(other), CTX)
    (first, second) = llm.calls
    assert "The hinge is firm." in first.user
    assert "The hinge is firm." not in second.user
    assert "Another made-up page." in second.user
    assert "Another made-up page." not in first.user


def test_the_prompt_lists_keys_and_definitions() -> None:
    llm = _empty_llm()
    GuardedExtractStage(llm).run(_input(keys=["battery_life", "keyboard_comfort"]), CTX)
    fixed = {c.key: c.definition for c in fallback_criteria("laptop").criteria}
    user = llm.calls[0].user
    assert f"- battery_life: {fixed['battery_life']}" in user
    assert f"- keyboard_comfort: {fixed['keyboard_comfort']}" in user
    assert "weight" not in user  # a fixed key the rubric did not ask for


def test_a_generated_key_gets_plain_words_for_its_definition() -> None:
    llm = _empty_llm()
    GuardedExtractStage(llm).run(_input(_thread_with_comment(), keys=["coding_performance"]), CTX)
    assert "- coding_performance: coding performance" in llm.calls[0].user


def test_the_system_prompt_stays_a_constant() -> None:
    """Our own lists go in the user message, so a prompt cache on the system text keeps working."""
    llm = _empty_llm()
    GuardedExtractStage(llm).run(_input(keys=["battery_life"]), CTX)
    GuardedExtractStage(llm).run(_input(keys=["weight"], category="headphones"), CTX)
    assert llm.calls[0].system == llm.calls[1].system
    assert "battery_life" not in llm.calls[0].system


def test_the_call_declares_its_task_and_difficulty() -> None:
    llm = _empty_llm()
    GuardedExtractStage(llm).run(_input(), CTX)
    (call,) = llm.calls
    assert (call.task, call.difficulty, call.search_id) == (LLMTask.EXTRACT_CLAIMS, Difficulty.LONG, "srch_test")


def test_a_reddit_call_asks_for_1200_output_tokens() -> None:
    llm = _empty_llm()
    GuardedExtractStage(llm).run(_input(_thread_with_comment()), CTX)
    assert llm.calls[0].max_output_tokens == 1200


def test_a_public_call_asks_for_2000_output_tokens() -> None:
    """These aren't booked against the pool. A live reply used 566 tokens on 15 mentions and no claims."""
    llm = _empty_llm()
    GuardedExtractStage(llm).run(_input(), CTX)
    assert llm.calls[0].max_output_tokens == 2000


def test_reddit_text_is_declared_reddit() -> None:
    llm = _empty_llm()
    GuardedExtractStage(llm).run(_input(_thread_with_comment()), CTX)
    assert llm.calls[0].data_class is DataClass.REDDIT


def test_web_text_is_declared_public() -> None:
    llm = _empty_llm()
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


# --- criterion keys and the category label (item 12) --------------------------------------------------


def test_a_lexicon_key_goes_into_a_public_call() -> None:
    llm = _empty_llm()
    GuardedExtractStage(llm).run(_input(keys=["battery_life", SHOPPER_KEY]), CTX)
    assert llm.calls[0].data_class is DataClass.PUBLIC
    assert "- battery_life: " in outside_data(llm.calls[0].user)  # the page's own text also holds the words


def test_a_shopper_made_key_stays_out_of_a_public_call() -> None:
    llm = _empty_llm()
    GuardedExtractStage(llm).run(_input(keys=["battery_life", SHOPPER_KEY]), CTX)
    assert "dialysis" not in llm.calls[0].system + llm.calls[0].user


def test_a_shopper_made_key_goes_into_a_reddit_call() -> None:
    llm = _empty_llm()
    GuardedExtractStage(llm).run(_input(_thread_with_comment(), keys=["battery_life", SHOPPER_KEY]), CTX)
    assert llm.calls[0].data_class is DataClass.REDDIT
    assert SHOPPER_KEY in llm.calls[0].user


def test_a_key_fixed_for_another_category_counts_as_shopper_made() -> None:
    """Only this category's fixed set is known to hold none of the shopper's words."""
    fixed = {c.key for c in fallback_criteria("laptop").criteria}
    assert "battery_life" in fixed
    assert allowed_criteria(_input(keys=["battery_life", "ink_cost"]), DataClass.PUBLIC).keys == ("battery_life",)


def test_a_claim_on_a_key_not_sent_is_dropped() -> None:
    raw = RawExtraction(mentions=[], claims=[_claim(criterion_key=SHOPPER_KEY)])
    llm = FakeLLMClient({LLMTask.EXTRACT_CLAIMS: raw})
    result = GuardedExtractStage(llm).run(_input(keys=["battery_life", SHOPPER_KEY]), CTX)
    assert result.claims == []


def test_the_same_claim_is_kept_when_the_key_was_sent() -> None:
    quote = QUOTE
    raw = RawExtraction(mentions=[], claims=[_claim(criterion_key=SHOPPER_KEY, quote=quote)])
    llm = FakeLLMClient({LLMTask.EXTRACT_CLAIMS: raw})
    result = GuardedExtractStage(llm).run(_input(_thread_with_comment(), keys=[SHOPPER_KEY]), CTX)
    assert [c.criterion_key for c in result.claims] == [SHOPPER_KEY]


def test_a_public_call_with_no_key_left_still_goes_out_for_mentions() -> None:
    llm = FakeLLMClient(
        {
            LLMTask.EXTRACT_CLAIMS: RawExtraction(
                mentions=[RawMention(raw_name="Nova Book 14", is_product=True)],
                claims=[_claim(criterion_key=SHOPPER_KEY)],
            )
        }
    )
    result = GuardedExtractStage(llm).run(_input(keys=[SHOPPER_KEY]), CTX)
    assert "dialysis" not in llm.calls[0].user
    assert [m.raw_name for m in result.mentions] == ["Nova Book 14"]
    assert result.claims == []


def test_a_known_category_goes_into_every_call() -> None:
    llm = _empty_llm()
    stage = GuardedExtractStage(llm)
    stage.run(_input(), CTX)
    stage.run(_input(_thread_with_comment()), CTX)
    assert all("Category: Laptop" in call.user for call in llm.calls)


def test_an_unknown_category_stays_out_of_a_public_call() -> None:
    llm = _empty_llm()
    GuardedExtractStage(llm).run(_input(category="zarvis_special_thing"), CTX)
    assert "zarvis" not in llm.calls[0].system + llm.calls[0].user
    assert "Category:" not in llm.calls[0].user


def test_an_unknown_category_goes_into_a_reddit_call() -> None:
    llm = _empty_llm()
    GuardedExtractStage(llm).run(_input(_thread_with_comment(), category="zarvis_special_thing"), CTX)
    assert "Category: zarvis special thing" in llm.calls[0].user


def _walk_every_provider(
    source: Source, category: str = "zarvis_special_thing"
) -> tuple[dict[Provider, FakeAdapter], StageContext]:
    """Every provider refuses, so the wrapper walks the whole route and each one on it sees the prompt."""
    config = load_llm_config()
    adapters = {p: FakeAdapter(p, replies=(ProviderError(p, "made up"),)) for p in Provider}
    client = WrapperLLMClient(config, dict(adapters), InMemoryCallLog(), limiters={})
    inp = _input(source, keys=["battery_life", SHOPPER_KEY], category=category)
    GuardedExtractStage(client, now=lambda: NOW).run(inp, CTX)
    return adapters, CTX


def test_a_shopper_made_key_never_reaches_a_provider_that_trains() -> None:
    config = load_llm_config()
    trained: set[Provider] = set()
    for source in (PAGE, _thread_with_comment()):
        adapters, _ = _walk_every_provider(source)
        for provider, adapter in adapters.items():
            for call in adapter.calls:
                text = call.system + call.user
                if config.provider(provider).trains_on_inputs:
                    trained.add(provider)
                    assert "dialysis" not in text and "zarvis" not in text, provider
    assert trained == {Provider.GEMINI, Provider.MISTRAL, Provider.DEEPSEEK, Provider.OPENROUTER}  # each was asked


def test_a_lexicon_key_still_reaches_a_provider_that_trains() -> None:
    adapters, _ = _walk_every_provider(PAGE, category="laptop")
    for provider in (Provider.GEMINI, Provider.MISTRAL, Provider.OPENROUTER):
        call = adapters[provider].calls[0]
        assert "- battery_life: " in outside_data(call.user), provider
        assert "dialysis" not in call.system + call.user, provider


def test_only_providers_that_do_not_train_see_a_shopper_made_key() -> None:
    config = load_llm_config()
    adapters, _ = _walk_every_provider(_thread_with_comment())
    saw = {p for p, a in adapters.items() if any(SHOPPER_KEY in call.user for call in a.calls)}
    assert saw == {Provider.GROQ}
    assert not config.provider(Provider.GROQ).trains_on_inputs


# --- what is accepted ------------------------------------------------------------------------------


def test_an_honest_claim_keeps_its_source() -> None:
    raw = RawExtraction(mentions=[RawMention(raw_name="Nova Book 14", is_product=True)], claims=[_claim()])
    result = to_extraction_result(raw, PAGE, KEYS, NOW)
    (claim,) = result.claims
    assert claim.quote == QUOTE
    assert claim.score == 8.0
    assert [m.raw_name for m in result.mentions] == ["Nova Book 14"]


def test_the_source_url_and_times_come_from_the_source() -> None:
    later = NOW + timedelta(hours=2)  # so the fetch time and the reading time can't be mistaken for each other
    (claim,) = to_extraction_result(RawExtraction(mentions=[], claims=[_claim()]), PAGE, KEYS, later).claims
    assert claim.source_id == PAGE.id
    assert claim.source_url == PAGE.url
    assert claim.retrieved_at == PAGE.fetched_at
    assert claim.extracted_at == later


def test_the_model_cannot_set_the_type_or_the_url() -> None:
    for extra in ({"claim_type": "spec"}, {"source_url": "https://attacker.example/"}):
        with pytest.raises(ValueError, match="Extra inputs"):
            _claim(**extra)


def test_the_claim_type_is_set_from_the_source() -> None:
    (claim,) = to_extraction_result(RawExtraction(mentions=[], claims=[_claim()]), PAGE, KEYS, NOW).claims
    assert claim.claim_type is ClaimType.EXPERIENCE


@pytest.mark.parametrize(
    ("given", "stored"),
    [
        pytest.param(True, "yes", id="true"),
        pytest.param(False, "no", id="false"),
        pytest.param("Yes", "yes", id="yes-as-text"),
        pytest.param("NO", "no", id="no-as-text"),
        pytest.param(" yes ", "yes", id="yes-with-spaces"),
    ],
)
def test_a_yes_or_no_fact_is_stored_as_text(given: object, stored: str) -> None:
    raw = RawExtraction(mentions=[], claims=[_claim(score=None, value=given)])
    (claim,) = to_extraction_result(raw, PAGE, KEYS, NOW).claims
    assert claim.value == stored


@pytest.mark.parametrize("given", [1.4, 16, "1.4 kg", "aluminium"])
def test_other_values_stay_as_given(given: object) -> None:
    raw = RawExtraction(mentions=[], claims=[_claim(score=None, value=given)])
    (claim,) = to_extraction_result(raw, PAGE, KEYS, NOW).claims
    assert claim.value == given


def test_the_same_claim_gets_the_same_id_and_counts_once() -> None:
    raw = RawExtraction(mentions=[], claims=[_claim(), _claim(), _claim(quote=QUOTE.upper())])
    first = to_extraction_result(raw, PAGE, KEYS, NOW)
    second = to_extraction_result(raw, PAGE, KEYS, NOW)
    assert len(first.claims) == 1
    assert first.claims[0].id == second.claims[0].id


def test_a_product_not_in_the_source_is_dropped() -> None:
    assert _result(_claim(product_mention="Kestrel Slim 15")) == []


def test_a_product_name_inside_another_word_is_dropped() -> None:
    source = review_source("https://www.reviews.example/e", text="An innovative design. The hinge is firm.")
    assert _result(_claim(product_mention="Nova", quote="The hinge is firm."), source=source) == []


def test_a_product_name_with_pattern_characters_is_taken_literally() -> None:
    source = review_source("https://www.reviews.example/h", text="The C++ Book lasts nine hours.")
    assert _result(_claim(product_mention="C++ Book", quote="lasts nine hours"), source=source) == ["C++ Book"]
    assert _result(_claim(product_mention="C.. Book", quote="lasts nine hours"), source=source) == []


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


def test_an_opinion_about_no_criterion_is_dropped() -> None:
    assert _result(_claim(criterion_key=None, score=None, value="good")) == []
    assert _result(_claim(criterion_key=None)) == []


def test_an_overall_criterion_is_dropped() -> None:
    assert _result(_claim(criterion_key="overall")) == []


def test_an_opinion_on_a_criterion_is_kept_with_its_score() -> None:
    (claim,) = to_extraction_result(RawExtraction(mentions=[], claims=[_claim()]), PAGE, KEYS, NOW).claims
    assert (claim.criterion_key, claim.score, claim.value) == ("battery_life", 8.0, None)


def test_a_claim_the_contract_refuses_is_dropped_alone() -> None:
    both = _claim(value="good")  # a value and a score: the contract allows exactly one
    assert _result(both, _claim(quote="The hinge is firm.", product_mention="Nova Book 14")) == ["Nova Book 14"]


def test_a_mention_not_in_the_source_is_dropped() -> None:
    raw = RawExtraction(mentions=[RawMention(raw_name="Kestrel Slim 15", is_product=True)], claims=[])
    assert to_extraction_result(raw, PAGE, KEYS, NOW).mentions == []


def test_a_repeated_mention_counts_once() -> None:
    raw = RawExtraction(
        mentions=[
            RawMention(raw_name="Nova Book 14", is_product=True),
            RawMention(raw_name="nova book 14", is_product=True),
        ],
        claims=[],
    )
    assert [m.raw_name for m in to_extraction_result(raw, PAGE, KEYS, NOW).mentions] == ["Nova Book 14"]


def test_a_claims_product_also_counts_as_a_mention() -> None:
    result = to_extraction_result(RawExtraction(mentions=[], claims=[_claim()]), PAGE, KEYS, NOW)
    assert [(m.raw_name, m.is_product) for m in result.mentions] == [("Nova Book 14", True)]


def test_a_full_mention_list_with_more_from_claims_stays_within_the_contract() -> None:
    names = [f"Model {i}" for i in range(100)] + ["Nova Book 14"]
    text = " ".join(f"{name}." for name in names)
    source = review_source("https://www.reviews.example/f", text=f"{text} {QUOTE}")
    raw = RawExtraction(mentions=[RawMention(raw_name=n, is_product=True) for n in names[:100]], claims=[_claim()])
    result = to_extraction_result(raw, source, KEYS, NOW)
    assert len(result.mentions) == 100
    assert len(result.claims) == 1


# --- products and kinds of product --------------------------------------------------------------------

FOAM = "Memory foam sleeves protect a laptop from knocks."
FOAM_PAGE = review_source("https://www.reviews.example/g", text=f"{FOAM} {QUOTE}")


def _foam(**overrides: object) -> RawClaim:
    return _claim(product_mention="memory foam", quote=FOAM, **overrides)


def test_memory_foam_is_kept_as_a_product_type() -> None:
    raw = RawExtraction(
        mentions=[RawMention(raw_name="memory foam", is_product=False)], claims=[_foam(is_product_type=True)]
    )
    result = to_extraction_result(raw, FOAM_PAGE, KEYS, NOW)
    assert [(m.raw_name, m.is_product) for m in result.mentions] == [("memory foam", False)]
    assert [(c.product_mention, c.is_product_type) for c in result.claims] == [("memory foam", True)]


def test_a_buyable_product_stays_a_product() -> None:
    raw = RawExtraction(mentions=[RawMention(raw_name="Nova Book 14", is_product=True)], claims=[_claim()])
    result = to_extraction_result(raw, FOAM_PAGE, KEYS, NOW)
    assert [m.is_product for m in result.mentions] == [True]
    assert [c.is_product_type for c in result.claims] == [False]


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(
            RawExtraction(mentions=[RawMention(raw_name="memory foam", is_product=False)], claims=[_foam()]),
            id="the-mention-says-so",
        ),
        pytest.param(
            RawExtraction(
                mentions=[RawMention(raw_name="Memory Foam", is_product=True)], claims=[_foam(is_product_type=True)]
            ),
            id="the-claim-says-so",
        ),
        pytest.param(RawExtraction(mentions=[], claims=[_foam(is_product_type=True)]), id="only-a-claim"),
    ],
)
def test_a_product_type_is_never_marked_as_a_product(raw: RawExtraction) -> None:
    """One word from the model that it's a kind of product is enough, so it can't reach the product list."""
    result = to_extraction_result(raw, FOAM_PAGE, KEYS, NOW)
    assert [m.is_product for m in result.mentions] == [False]
    assert [c.is_product_type for c in result.claims] == [True]


# --- the stage -------------------------------------------------------------------------------------


def test_a_dropped_source_is_logged_with_no_text(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="app.extract"):
        GuardedExtractStage(FakeLLMClient()).run(_input(), CTX)
    assert "dropped" in caplog.text
    assert "hinge" not in caplog.text


def test_a_long_title_is_cut_in_the_prompt() -> None:
    """A title isn't counted in the source's cap, so a long one could push the data block's end past the limit."""
    llm = _empty_llm()
    long_title = chr(0x939) * 300
    source = review_source("https://www.reviews.example/i", title=long_title, text=QUOTE)
    GuardedExtractStage(llm).run(_input(source), CTX)
    assert chr(0x939) * MAX_TITLE_CHARS in llm.calls[0].user
    assert chr(0x939) * (MAX_TITLE_CHARS + 1) not in llm.calls[0].user


def test_an_unavailable_llm_drops_the_source() -> None:
    result = GuardedExtractStage(FakeLLMClient()).run(_input(), CTX)
    assert (result.source_id, result.claims, result.mentions) == (PAGE.id, [], [])


def test_the_stub_is_the_fallback() -> None:
    stage = GuardedExtractStage(FakeLLMClient())
    assert isinstance(stage.stub, ExtractStage)
    assert stage.fallback(_input(), CTX, RuntimeError("x")) == stage.stub.fallback(_input(), CTX, RuntimeError("x"))


def test_the_fallback_gives_nothing_rather_than_a_guess() -> None:
    result = GuardedExtractStage(FakeLLMClient()).fallback(_input(), CTX, RuntimeError("x"))
    assert (result.source_id, result.claims, result.mentions) == (PAGE.id, [], [])


@pytest.mark.parametrize("status", [StageStatus.FALLBACK, StageStatus.TIMEOUT])
def test_a_failed_source_is_not_labelled_a_sample(status: StageStatus) -> None:
    """Its fallback gives nothing, not sample data, so one bad source must not call real claims a sample."""
    report = StageReport(stage=StageName.EXTRACT, status=status, duration_ms=1, fallback_reason="took too long")
    assert not made_by_placeholder(GuardedExtractStage(FakeLLMClient()), report)


def test_the_stage_uses_the_tier_map() -> None:
    """Research calls a web page editorial, and the map knows this domain is a lab."""
    lab = review_source("https://www.rtings.com/laptop/a", text=QUOTE)
    llm = FakeLLMClient({LLMTask.EXTRACT_CLAIMS: RawExtraction(mentions=[], claims=[_claim()])})
    (claim,) = GuardedExtractStage(llm).run(_input(lab), CTX).claims
    assert claim.claim_type is ClaimType.SPEC


def test_the_real_stage_is_not_a_placeholder() -> None:
    report = StageReport(stage=StageName.EXTRACT, status=StageStatus.OK, duration_ms=1)
    assert not made_by_placeholder(GuardedExtractStage(FakeLLMClient()), report)
