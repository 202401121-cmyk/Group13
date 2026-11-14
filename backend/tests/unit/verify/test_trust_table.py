"""US-16: the one trust table (backend/config/trust.yaml), DR-03's order, the affiliate rule and Reddit as community."""

from __future__ import annotations

import logging
from itertools import pairwise
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from app.contracts import SourceTier, SourceType
from app.extract.settings import load_extract_settings
from app.verify.trust import (
    TRUST_PATH,
    TrustTable,
    describe_sources,
    load_trust_table,
    site_key,
    source_tier,
    source_trust,
)
from tests.unit.verify.build import reddit, source

CONFIG = Path(__file__).resolve().parents[3] / "config"
INDIAN_REVIEW_SITES = ["91mobiles.com", "smartprix.com", "digit.in", "gadgets360.com"]


@pytest.fixture(scope="module")
def table() -> TrustTable:
    return load_trust_table()


def test_the_table_has_the_indian_review_sites_and_every_research_site(table: TrustTable) -> None:
    research = yaml.safe_load((CONFIG / "research.yaml").read_text(encoding="utf-8"))["review_domains"]
    for domain in [*INDIAN_REVIEW_SITES, "rtings.com", *research]:
        assert domain in table.sites, domain
        assert 0 <= table.sites[domain].score <= 100
    assert TRUST_PATH == CONFIG / "trust.yaml"


def test_the_table_agrees_with_extracts_tiers(table: TrustTable) -> None:
    """Extract reads this table's tiers (S3-7), so both stages always agree on every site's tier."""
    extract = load_extract_settings(env={}).domain_tiers
    assert extract == table.domain_tiers()
    for domain, tier in extract.items():
        assert domain in table.sites, domain
        assert table.domain_tiers()[domain] is tier, domain


def test_a_category_adjusts_a_sites_score(table: TrustTable) -> None:
    page = source("https://www.gsmarena.com/nova_phone_5-review-1.php")
    plain = source_trust(page, table)
    assert source_trust(page, table, "phone") == plain + table.sites["gsmarena.com"].categories["phone"]
    assert source_trust(page, table, "laptop") < plain
    assert source_trust(page, table, "kettle") == plain  # a category it has no adjustment for


@pytest.mark.parametrize("folder", ["advisor", "deals"])
def test_an_affiliate_page_starts_lower_than_the_same_sites_review(table: TrustTable, folder: str) -> None:
    review = source("https://www.91mobiles.com/nova-phone-5-review")
    affiliate = source(f"https://www.91mobiles.com/{folder}/best-phones-under-20000")
    assert source_trust(affiliate, table) == source_trust(review, table) - table.affiliate_penalty
    assert source_trust(affiliate, table) < source_trust(review, table)
    assert site_key(affiliate, table) == site_key(review, table) == "91mobiles.com"


def test_unknown_sites_take_their_types_default_in_dr03_order(table: TrustTable) -> None:
    kinds = [SourceType.MANUFACTURER, SourceType.LAB, SourceType.REVIEW_SITE, SourceType.RETAILER]
    scores = [source_trust(source("https://made-up-site.example/page", kind), table) for kind in kinds]
    scores.insert(3, source_trust(reddit(), table))
    assert scores == sorted(scores, reverse=True)  # maker > lab > editorial > community > shop
    assert len(set(scores)) == len(scores)
    maker = source("https://kestrel.example.com/slim-15/specs", SourceType.MANUFACTURER)
    assert source_trust(maker, table) == max(table.defaults.values())  # makers' own pages score highest
    assert source_tier(maker, table) is SourceTier.MANUFACTURER


def test_reddit_is_never_editorial(table: TrustTable, tmp_path: Path) -> None:
    thread = reddit("r/IndianGaming")
    assert source_tier(thread, table) is SourceTier.COMMUNITY
    assert site_key(thread, table) == "reddit:r/indiangaming"
    forum = source("https://forum.digit.in/threads/nova-phone-5.12345/")
    assert source_tier(forum, table) is SourceTier.COMMUNITY  # a forum on an editorial site is community
    for name in ("reddit.com", "old.reddit.com", "redd.it"):
        bad = tmp_path / f"{name}.yaml"
        bad.write_text(f"sites:\n  {name}: {{score: 90, tier: editorial}}\n", encoding="utf-8")
        with pytest.raises(ValueError, match="community"):
            TrustTable.model_validate(yaml.safe_load(bad.read_text(encoding="utf-8")))
    deals = reddit("r/deals")
    assert source_trust(deals, table) == table.defaults[SourceType.REDDIT]  # a subreddit is not an affiliate page


def test_youtube_keeps_the_tier_research_gives_it(table: TrustTable) -> None:
    video = source("https://www.youtube.com/watch?v=madeup00000", SourceType.YOUTUBE, SourceTier.EDITORIAL)
    assert source_tier(video, table) is SourceTier.EDITORIAL
    assert source_trust(video, table) == table.defaults[SourceType.YOUTUBE]


def test_a_forum_on_a_sites_own_domain_is_a_voice_of_its_own(table: TrustTable) -> None:
    spec = source("https://www.asus.com/laptops/nova/techspec/", SourceType.MANUFACTURER)
    forum = source("https://www.asus.com/community/forum/nova-battery/", SourceType.WEB_SEARCH)
    described = describe_sources([spec, forum], table)
    assert described[spec.id] == ("asus.com", SourceTier.MANUFACTURER, 95.0)
    assert described[forum.id] == ("asus.com#community", SourceTier.COMMUNITY, table.defaults[SourceType.REDDIT])


def test_every_category_keeps_dr03_order(table: TrustTable) -> None:
    """After any category's adjustments, every maker still outranks every lab, which outranks every editorial site,
    which outranks every community site."""
    categories = {c for site in table.sites.values() for c in site.categories} | {None}
    order = ["manufacturer", "lab", "editorial", "community"]
    for category in sorted(categories, key=str):
        scores: dict[str, list[float]] = {tier: [] for tier in order}
        for site in table.sites.values():
            if site.tier in scores:
                adjust = site.categories.get(category, 0.0) if category else 0.0
                scores[site.tier].append(site.score + adjust)
        for higher, lower in pairwise(order):
            assert min(scores[higher]) > max(scores[lower]), (category, higher, lower)
    defaults = table.defaults
    by_type = [SourceType.MANUFACTURER, SourceType.LAB, SourceType.REVIEW_SITE, SourceType.REDDIT]
    assert [defaults[t] for t in by_type] == sorted((defaults[t] for t in by_type), reverse=True)


def test_a_broken_table_falls_back_to_the_defaults(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    broken = tmp_path / "trust.yaml"
    broken.write_text("sites: [this is not a table\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        table = load_trust_table(broken)
    assert table == TrustTable()
    assert table.defaults[SourceType.MANUFACTURER] == 95  # the built-in defaults still keep DR-03's order
    assert "trust.yaml" in caplog.text
    missing = load_trust_table(tmp_path / "missing.yaml")
    assert missing == TrustTable()


def test_the_thresholds_are_the_teams_choices(table: TrustTable) -> None:
    assert (table.unknown_below, table.dispute_share, table.affiliate_penalty) == (40, 0.30, 15)
    assert (table.max_rounds, table.min_weight_change) == (10, 0.01)
    assert table.tolerance("price_inr").relative == 0.03
    assert table.tolerance("ram_gb").relative == 0
    assert table.tolerance("battery_life").relative == table.value_tolerance
