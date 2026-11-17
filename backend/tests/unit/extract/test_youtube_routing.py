"""A YouTube video read through extraction (US-38): with viewers' comments it is reddit-class text, so only providers
that never train on inputs may read it (P-23, build rule 8); with none it is a public page. Made-up video and comments.
"""

from __future__ import annotations

from app.contracts import DataClass, ExtractInput, LLMTask, Provider, StageContext
from app.extract import GuardedExtractStage
from app.extract.parsing import RawExtraction
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.research.youtube import _Video, video_source
from tests.fakes.llm import FakeLLMClient
from tests.fakes.obedient import ObedientAdapter
from tests.fakes.reddit import NOW

CTX = StageContext(search_id="srch_test")
KEYS = ["battery_life", "sound_quality", "value_for_money"]


def _video(comments: list[str]) -> ExtractInput:
    made = _Video("vid00000001x", "Made-up earbuds review", "Made-up Channel", "We test the Nova Buds.", NOW)
    source = video_source(made, comments, NOW)
    assert source is not None
    return ExtractInput(source=source, criterion_keys=KEYS, category_label="earbuds")


def test_a_video_with_comments_is_read_as_reddit_class_text() -> None:
    llm = FakeLLMClient({LLMTask.EXTRACT_CLAIMS: RawExtraction(mentions=[], claims=[])})
    GuardedExtractStage(llm).run(_video(["The battery lasts two days."]), CTX)
    assert llm.calls[0].data_class is DataClass.REDDIT


def test_a_video_with_no_comments_is_read_as_a_public_page() -> None:
    llm = FakeLLMClient({LLMTask.EXTRACT_CLAIMS: RawExtraction(mentions=[], claims=[])})
    GuardedExtractStage(llm).run(_video([]), CTX)
    assert llm.calls[0].data_class is DataClass.PUBLIC


def test_viewers_comments_never_reach_a_provider_that_trains_on_them() -> None:
    """Build rule 8, through the real routing: every provider is offered, and only the safe ones may be asked."""
    config = load_llm_config()
    adapters = {provider: ObedientAdapter(provider) for provider in Provider}
    client = WrapperLLMClient(config, dict(adapters), InMemoryCallLog(), limiters={})
    GuardedExtractStage(client, now=lambda: NOW).run(_video(["The case cracked after a week."]), CTX)
    asked = {provider for provider, adapter in adapters.items() if adapter.prompts}
    assert asked
    assert not [p for p in asked if config.provider(p).trains_on_inputs]
    assert asked <= {Provider.GROQ}  # the reddit route is Groq only (config/models.yaml)


# --- the same quote rule as every source (criterion 3) ---------------------------------------------------------------


def _quotes_kept(*quotes: str) -> list[str]:
    from app.extract.parsing import RawClaim, to_extraction_result

    source = _video(["The battery lasts two days for me.", "The fit is loose when I run."]).source
    claims = [
        RawClaim(
            product_mention="Nova Buds",  # named in the description: a claim's product must be in its source
            criterion_key="battery_life",
            score=8.0,
            quote=quote,
            polarity="positive",
        )
        for quote in quotes
    ]
    result = to_extraction_result(RawExtraction(mentions=[], claims=claims), source, KEYS, NOW)
    return [claim.quote for claim in result.claims]


def test_a_quote_from_the_videos_title_description_or_one_comment_is_kept() -> None:
    kept = _quotes_kept("Made-up earbuds review", "We test the Nova Buds.", "The battery lasts two days for me.")
    assert kept == ["Made-up earbuds review", "We test the Nova Buds.", "The battery lasts two days for me."]


def test_a_quote_not_in_the_text_or_run_across_two_comments_is_dropped() -> None:
    assert _quotes_kept("The battery lasts a week.") == []
    assert _quotes_kept("two days for me. The fit is loose") == []  # two viewers' words never make one quote
