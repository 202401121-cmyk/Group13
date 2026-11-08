"""Code sets a claim's type from its source, never the model (US-14, DR-05)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.contracts import ClaimType, Source, SourceTier, SourceType, examples
from app.extract.parsing import RawClaim, RawExtraction, to_extraction_result
from app.extract.settings import DEFAULT_CONFIG_PATH, ExtractSettings, load_extract_settings
from app.extract.tiers import claim_type_for, tier_of
from app.llm import load_llm_config
from tests.fakes.reddit import NOW
from tests.fakes.research import review_source, thread, web_source

DOMAINS = load_extract_settings(env={}).domain_tiers
QUOTE = "Nova Book 14 lasts nine hours."


def _web(url: str) -> Source:
    return web_source(url, text=QUOTE)


def _claim_type(source: Source) -> ClaimType:
    raw = RawClaim(
        product_mention="Nova Book 14", criterion_key="battery_life", score=8.0, quote=QUOTE, polarity="neutral"
    )
    extraction = RawExtraction(mentions=[], claims=[raw])
    (claim,) = to_extraction_result(extraction, source, ["battery_life"], NOW, DOMAINS).claims
    return claim.claim_type


def test_a_reddit_source_is_typed_experience() -> None:
    source = thread("aaa", text=QUOTE)
    assert tier_of(source, DOMAINS) is SourceTier.COMMUNITY
    assert _claim_type(source) is ClaimType.EXPERIENCE


@pytest.mark.parametrize("url", ["https://www.rtings.com/laptop/reviews/a", "https://www.notebookcheck.net/a.html"])
def test_a_lab_page_is_typed_spec(url: str) -> None:
    assert tier_of(_web(url), DOMAINS) is SourceTier.LAB
    assert _claim_type(_web(url)) is ClaimType.SPEC


def test_a_page_research_typed_as_a_lab_stays_one() -> None:
    page = review_source("https://labs.example/a", text=QUOTE).model_dump()
    source = Source.model_validate({**page, "type": SourceType.LAB, "tier": SourceTier.LAB})
    assert _claim_type(source) is ClaimType.SPEC


def test_a_manufacturer_page_is_typed_spec() -> None:
    url = "https://www.dell.com/en-in/shop/laptops/a"
    assert tier_of(_web(url), DOMAINS) is SourceTier.MANUFACTURER
    assert _claim_type(_web(url)) is ClaimType.SPEC


def test_a_page_research_typed_as_a_manufacturer_stays_one() -> None:
    maker = next(s for s in examples.sources() if s.type is SourceType.MANUFACTURER)
    assert tier_of(maker, DOMAINS) is SourceTier.MANUFACTURER
    assert claim_type_for(maker, DOMAINS) is ClaimType.SPEC


def test_an_editorial_page_is_typed_experience() -> None:
    source = review_source("https://www.gadgets360.com/a", text=QUOTE)
    assert tier_of(source, DOMAINS) is SourceTier.EDITORIAL
    assert _claim_type(source) is ClaimType.EXPERIENCE


@pytest.mark.parametrize("url", ["https://www.quora.com/a", "https://forums.tomshardware.com/threads/a"])
def test_a_forum_domain_is_typed_experience(url: str) -> None:
    assert tier_of(_web(url), DOMAINS) is SourceTier.COMMUNITY
    assert _claim_type(_web(url)) is ClaimType.EXPERIENCE


@pytest.mark.parametrize(
    "url",
    [
        "https://discussions.apple.com/thread/1",
        "https://community.hp.com/t5/a",
        "https://forums.lenovo.com/t5/a",
        "https://us.community.samsung.com/t5/a",
        "https://answers.microsoft.com/en-us/a",
        "https://forum.nobody-has-listed-this.example/a",
        "https://forum-en.msi.com/a",
        "https://rog-forum.asus.com/t5/a",
        "https://www.dell.com/community/a",
        "https://www.rtings.com/discussions/a",
        "https://h30434.www3.hp.com/t5/a",
        "https://zentalk.asus.com/a",
        "https://c.mi.com/a",
    ],
)
def test_a_forum_on_any_domain_is_typed_experience(url: str) -> None:
    """An owner's post on a maker's forum is not the maker's spec."""
    assert tier_of(_web(url), DOMAINS) is SourceTier.COMMUNITY
    assert _claim_type(_web(url)) is ClaimType.EXPERIENCE


@pytest.mark.parametrize(
    ("url", "tier"),
    [
        pytest.param("https://www.dell.com/shop/community-edition", SourceTier.MANUFACTURER, id="inside-a-path-word"),
        pytest.param("https://support.dell.com/a", SourceTier.MANUFACTURER, id="a-support-page"),
        pytest.param("https://forum.example/a", SourceTier.EDITORIAL, id="forum-as-the-site-name"),
    ],
)
def test_the_word_forum_elsewhere_changes_nothing(url: str, tier: SourceTier) -> None:
    assert tier_of(_web(url), DOMAINS) is tier


def test_a_retailer_is_typed_experience() -> None:
    """A shop's listing is unchecked, so it must not count as a spec."""
    url = "https://www.flipkart.com/a/p/b"
    assert tier_of(_web(url), DOMAINS) is SourceTier.RETAILER
    assert _claim_type(_web(url)) is ClaimType.EXPERIENCE


def test_an_unknown_domain_counts_as_editorial() -> None:
    assert tier_of(_web("https://nobody-has-listed-this.example/a"), DOMAINS) is SourceTier.EDITORIAL


@pytest.mark.parametrize(
    ("url", "tier"),
    [
        pytest.param("https://dell.com/a", SourceTier.MANUFACTURER, id="the-domain"),
        pytest.param("https://support.dell.com/a", SourceTier.MANUFACTURER, id="a-subdomain"),
        pytest.param("https://DELL.com/a", SourceTier.MANUFACTURER, id="in-capitals"),
        pytest.param("https://notdell.com/a", SourceTier.EDITORIAL, id="a-lookalike"),
        pytest.param("https://dell.com.evil.example/a", SourceTier.EDITORIAL, id="the-domain-as-a-prefix"),
        pytest.param("https://evil.example/dell.com/a", SourceTier.EDITORIAL, id="the-domain-in-the-path"),
    ],
)
def test_a_subdomain_matches_and_a_lookalike_does_not(url: str, tier: SourceTier) -> None:
    assert tier_of(_web(url), DOMAINS) is tier


def test_the_most_specific_domain_wins() -> None:
    domains = {"example.com": SourceTier.EDITORIAL, "labs.example.com": SourceTier.LAB}
    assert tier_of(_web("https://a.labs.example.com/b"), domains) is SourceTier.LAB
    assert tier_of(_web("https://www.example.com/b"), domains) is SourceTier.EDITORIAL


def test_reddit_stays_community_whatever_the_map_says() -> None:
    source = thread("aaa", text=QUOTE)
    assert tier_of(source, {"reddit.com": SourceTier.LAB}) is SourceTier.COMMUNITY


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("tiers: [not, a, mapping", id="broken-yaml"),
        pytest.param("- a list", id="not-a-mapping"),
        pytest.param("tiers:\n  guru: [a.example]\n", id="an-unknown-tier"),
        pytest.param("reddit_output_tokens: 0\n", id="a-bad-number"),
        pytest.param("surprise: 1\n", id="an-unknown-key"),
        pytest.param('tiers:\n  lab: ["https://a.example/"]\n', id="a-url-for-a-domain"),
    ],
)
def test_a_broken_file_counts_every_domain_as_editorial(tmp_path: Path, content: str) -> None:
    path = tmp_path / "extract.yaml"
    path.write_text(content, encoding="utf-8")
    settings = load_extract_settings(path, env={})
    assert settings.domain_tiers == {}
    assert tier_of(_web("https://www.rtings.com/a"), settings.domain_tiers) is SourceTier.EDITORIAL
    assert settings.reddit_output_tokens == ExtractSettings().reddit_output_tokens


def test_a_missing_file_gives_the_defaults(tmp_path: Path) -> None:
    assert load_extract_settings(tmp_path / "nothing.yaml", env={}).domain_tiers == {}


def test_the_settings_come_from_the_config_file() -> None:
    settings = load_extract_settings(DEFAULT_CONFIG_PATH, env={})
    assert settings.reddit_output_tokens == 1200
    assert settings.public_output_tokens == 2000
    assert settings.max_sources_per_search == 40
    assert settings.reddit_tokens_per_search == 18_000


def test_the_reddit_budget_comes_from_the_models_file(monkeypatch: pytest.MonkeyPatch) -> None:
    real = load_llm_config()
    pool = {**real.pool.model_dump(), "reddit_tokens_per_search": 1234}
    changed = real.model_validate({**real.model_dump(), "pool": pool})
    monkeypatch.setattr("app.extract.settings.load_llm_config", lambda: changed)
    assert load_extract_settings(env={}).reddit_tokens_per_search == 1234


def test_the_env_switch_lowers_the_cap() -> None:
    assert load_extract_settings(env={"EXTRACT_MAX_SOURCES": "8"}).max_sources_per_search == 8


@pytest.mark.parametrize("value", ["", "lots", "0", "-3", "1000"])
def test_a_bad_env_value_is_ignored(value: str) -> None:
    assert load_extract_settings(env={"EXTRACT_MAX_SOURCES": value}).max_sources_per_search == 40


def test_the_env_switch_works_when_the_file_is_broken(tmp_path: Path) -> None:
    path = tmp_path / "extract.yaml"
    path.write_text("[", encoding="utf-8")
    assert load_extract_settings(path, env={"EXTRACT_MAX_SOURCES": "8"}).max_sources_per_search == 8
