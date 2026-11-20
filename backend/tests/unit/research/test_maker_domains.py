"""Maker sites (S4-17, US-10 leftovers): the brands in ml/labelling/conflicts.csv have their own sites listed.

Each was found by a search and opened to confirm it is the maker's own (the hand-over records the address and date).
A maker site is never a store or a reseller, so none is also a retailer.
"""

from __future__ import annotations

import csv
from pathlib import Path
from urllib.parse import urlparse

from app.research.settings import load_research_settings, maker_domains_of

CONFLICTS = Path(__file__).resolve().parents[4] / "ml" / "labelling" / "conflicts.csv"


def test_the_new_brands_have_their_own_sites() -> None:
    makers = load_research_settings(env={}).maker_domains
    assert makers["noise"] == ("gonoise.com",)
    assert makers["fire-boltt"] == ("fireboltt.com",)
    assert makers["instant"] == ("instantpot.com", "instantpot.com.au")


def test_every_maker_page_the_conflicts_cite_is_on_a_listed_maker_site() -> None:
    domains = maker_domains_of(load_research_settings(env={}))
    with CONFLICTS.open(encoding="utf-8", newline="") as f:
        cited = {
            (urlparse(word.rstrip(".,'")).hostname or "").removeprefix("www.")
            for row in csv.DictReader(f)
            for word in row["how_established"].split()
            if word.startswith("http") and "maker page" in row["how_established"]
        }
    # amd.com is cited for a laptop's processor: a part's maker, not a brand a shopper buys, so it isn't listed.
    brands = cited - {"amd.com"}
    assert {"gonoise.com", "fireboltt.com", "instantpot.com.au"} <= brands
    assert all(any(host == d or host.endswith("." + d) for d in domains) for host in brands), sorted(brands)


def test_no_maker_site_is_a_retailer() -> None:
    settings = load_research_settings(env={})
    assert not set(maker_domains_of(settings)) & set(settings.retailer_domains)
