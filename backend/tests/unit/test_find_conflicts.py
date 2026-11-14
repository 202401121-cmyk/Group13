"""scripts/find_conflicts.py (US-16, S3-16): candidate conflicts from recorded claims, and answers proposed from the
maker's own page. Recorded claims and fake readers only: no search, no model, no network."""

from __future__ import annotations

import csv
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import httpx
import pytest

from app.contracts import Claim, Provider, Source, SourceType
from app.llm.errors import ProviderError
from app.llm.health import ProviderHealth
from app.verify.trust import default_trust_table
from scripts import find_conflicts as fc
from tests.unit.verify.build import claim, match, reddit, source

TABLE = default_trust_table()
NOVA = "Nova Book 14"


def found(*claims: Claim, extra: Sequence[Source] = ()) -> fc.Found:
    sources = {c.source_id: _source_of(c) for c in claims}
    sources |= {s.id: s for s in extra}
    return fc.Found(category="laptop", matched=match(NOVA), claims=list(claims), sources=sources)


_SOURCES: dict[str, Source] = {}


def page(url: str, kind: SourceType = SourceType.REVIEW_SITE, text: str | None = None) -> Source:
    made = source(url, kind, text=text)
    _SOURCES[made.id] = made
    return made


def _source_of(c: Claim) -> Source:
    return _SOURCES[c.source_id]


def hours(src: Source, value: float, quote: str | None = None) -> Claim:
    return claim(src, NOVA, "battery_life", value=value, unit="h", quote=quote or f"Battery lasts {value:g} hours.")


def candidates(f: fc.Found, known: set[tuple[str, str]] | None = None) -> list[fc.Candidate]:
    return fc.find_candidates(f, TABLE, known or set())


# --- which product and criterion is a candidate -------------------------------------------------------------------


def test_two_sites_agreeing_within_tolerance_is_no_candidate() -> None:
    a = hours(page("https://techdesk.example/nova"), 10)
    b = hours(page("https://gadgets.example/nova"), 10.3)  # 3 %, inside battery life's 5 %
    assert candidates(found(a, b)) == []


def test_one_site_repeated_is_no_candidate() -> None:
    a = hours(page("https://techdesk.example/nova"), 10)
    b = hours(page("https://shop.techdesk.example/nova-14"), 14)  # another page, the same site
    assert candidates(found(a, b)) == []


def test_two_sites_disagreeing_beyond_tolerance_is_a_candidate() -> None:
    a = hours(page("https://techdesk.example/nova"), 10)
    b = hours(page("https://gadgets.example/nova"), 14)
    (found_one,) = candidates(found(a, b))
    assert (found_one.product, found_one.criterion, found_one.unit) == (NOVA, "battery_life", "h")
    assert sorted((s.site, s.value) for s in found_one.sides) == [("gadgets.example", 14), ("techdesk.example", 10)]


def test_reddit_is_never_a_side() -> None:
    thread = reddit()
    _SOURCES[thread.id] = thread
    a = hours(page("https://techdesk.example/nova"), 10)
    on_reddit = hours(thread, 16)
    assert candidates(found(a, on_reddit)) == []  # one site and Reddit: no conflict
    b = hours(page("https://gadgets.example/nova"), 14)
    (with_two,) = candidates(found(a, b, on_reddit))
    assert all("reddit" not in s.site and "reddit" not in s.url for s in with_two.sides)
    assert len(with_two.sides) == 2


def test_a_claim_in_another_unit_is_left_out() -> None:
    a = hours(page("https://techdesk.example/nova"), 10)
    b = hours(page("https://gadgets.example/nova"), 14)
    in_minutes = claim(
        page("https://bench.example/nova"), NOVA, "battery_life", value=900, unit="min", quote="900 min."
    )
    (one,) = candidates(found(a, b, in_minutes))
    assert {s.site for s in one.sides} == {"techdesk.example", "gadgets.example"}  # tools never convert (DATA.md 9.3)


def test_a_product_and_criterion_already_in_conflicts_csv_is_skipped() -> None:
    a = hours(page("https://techdesk.example/nova"), 10)
    b = hours(page("https://gadgets.example/nova"), 14)
    assert candidates(found(a, b), known={(NOVA.casefold(), "battery_life")}) == []


def test_the_funnel_says_where_a_searchs_claims_drop_out() -> None:
    thread = reddit()
    _SOURCES[thread.id] = thread
    techdesk = page("https://techdesk.example/nova")
    gadgets = page("https://gadgets.example/nova")
    kept = [hours(techdesk, 10), hours(gadgets, 14)]
    dropped = [
        hours(thread, 16),  # Reddit
        claim(techdesk, NOVA, None, value=10, unit="h", quote="Battery lasts 10 hours."),  # no criterion
        claim(techdesk, NOVA, "build", score=4.0, quote="Battery lasts 10 hours."),  # an opinion: no figure
        claim(gadgets, "Orbit Pad 11", "battery_life", value=9, unit="h", quote="Battery lasts 14 hours."),  # unmatched
        claim(page("https://bench.example/nova"), NOVA, "ram_gb", value=16, unit="GB", quote="16 GB."),  # one site
    ]
    funnel = fc.Funnel()
    assert len(fc.find_candidates(found(*kept, *dropped), TABLE, set(), funnel)) == 1
    assert (funnel.claims, funnel.with_figure, funnel.on_a_product) == (7, 4, 3)
    assert (funnel.on_two_sites, funnel.already_known, funnel.candidates) == (1, 0, 1)
    assert str(funnel).startswith("7 claims, 4 with a figure")

    again = fc.Funnel()
    fc.find_candidates(found(*kept), TABLE, {(NOVA.casefold(), "battery_life")}, again)
    assert (again.already_known, again.candidates) == (1, 0)


# --- the maker's page -------------------------------------------------------------------------------------------------


def one_candidate() -> fc.Candidate:
    (c,) = candidates(
        found(hours(page("https://techdesk.example/nova"), 10), hours(page("https://gadgets.example/nova"), 14))
    )
    return c


MAKER_URL = "https://nova.example/book-14/specs"


def test_a_maker_page_stating_it_proposes_the_answer_with_its_link_and_exact_words() -> None:
    words = "Battery life: up to 11 hours of local video playback."
    maker = page(MAKER_URL, SourceType.MANUFACTURER, text=f"Nova Book 14 specs. {words} Weight 1.2 kg.")
    stated = claim(maker, NOVA, "battery_life", value=11, unit="h", quote=words)
    proposal = fc.propose(one_candidate(), maker, [stated])
    assert (proposal.answer, proposal.url) == (11, MAKER_URL)
    assert proposal.words is not None and proposal.words in words and "11 hours" in proposal.words
    said = fc.how_established(proposal)
    assert said.startswith("PROPOSED: ") and MAKER_URL in said and f"'{proposal.words}'" in said
    assert len(said) <= 300


def test_a_maker_page_without_the_figure_gives_a_candidate_with_no_proposed_answer() -> None:
    maker = page(MAKER_URL, SourceType.MANUFACTURER, text="Nova Book 14. Thin, light and quiet.")
    proposal = fc.propose(one_candidate(), maker, [])
    assert (proposal.answer, proposal.url, proposal.words) == (None, MAKER_URL, None)
    assert fc.how_established(proposal) == f"PROPOSED: no answer: the maker page {MAKER_URL} doesn't state it in h."
    rows = fc.candidate_rows(one_candidate(), proposal, "c900", TABLE)
    assert {r["known_answer"] for r in rows} == {""}


def test_a_figure_not_in_its_quote_is_never_proposed() -> None:
    maker = page(MAKER_URL, SourceType.MANUFACTURER, text="Nova Book 14 lasts all day.")
    vague = claim(maker, NOVA, "battery_life", value=11, unit="h", quote="Nova Book 14 lasts all day.")
    assert fc.propose(one_candidate(), maker, [vague]).answer is None


def test_a_figure_in_another_unit_or_for_another_product_is_never_proposed() -> None:
    maker = page(MAKER_URL, SourceType.MANUFACTURER, text="Up to 660 minutes. The Nova Book 16 lasts 13 hours.")
    in_minutes = claim(maker, NOVA, "battery_life", value=660, unit="min", quote="Up to 660 minutes.")
    other = claim(maker, "Nova Book 16", "battery_life", value=13, unit="h", quote="The Nova Book 16 lasts 13 hours.")
    assert fc.propose(one_candidate(), maker, [in_minutes, other]).answer is None


def test_no_maker_page_gives_a_candidate_with_no_proposed_answer() -> None:
    proposal = fc.propose(one_candidate(), None, [])
    assert (proposal.answer, proposal.url) == (None, None)
    assert fc.how_established(proposal) == "PROPOSED: no answer: no maker page found."


def test_a_number_in_a_model_name_is_never_the_figure() -> None:
    maker = page(MAKER_URL, SourceType.MANUFACTURER, text="Nova Book 14 specs.")
    named = claim(maker, NOVA, "battery_life", value=14, unit="h", quote="Nova Book 14 specs.")
    assert fc.propose(one_candidate(), maker, [named]).answer is None
    review = page("https://bench.example/nova", text="Nova Book 14 review. It lasted 14 hours.")
    other = page("https://gadgets.example/nova-14")
    (found_one,) = candidates(
        found(hours(review, 14, quote="Nova Book 14 review. It lasted 14 hours."), hours(other, 10))
    )
    rows = fc.candidate_rows(found_one, fc.Proposal(), "c900", TABLE)
    assert {r["claim_site"]: r["claim_quote"] for r in rows}["bench.example"] == "It lasted 14 hours."


def test_a_figure_with_its_currency_or_glued_unit_is_found() -> None:
    assert fc._words_around("Price: ₹49,990 at launch.", 49990, "inr", 200) == "Price: ₹49,990 at launch."
    assert fc._words_around("MRP Rs. 52,000 only.", 52000, "inr", 200) == "MRP Rs. 52,000 only."
    assert fc._words_around("It has 16GB of RAM.", 16, "gb", 200) == "It has 16GB of RAM."
    assert fc._words_around("An 11-hour battery.", 11, "h", 200) == "An 11-hour battery."
    assert fc._words_around("Intel Core i7 inside.", 7, "h", 200) is None
    assert fc._words_around("Weighs 1.2 kg.", 2, "kg", 200) is None  # the 2 after "1." is no figure of its own


def test_a_line_break_or_hidden_character_in_a_quote_never_reaches_the_file() -> None:
    table_text = "Battery life\nUp to 11 hours​\tof video"
    maker = page(MAKER_URL, SourceType.MANUFACTURER, text=table_text)
    stated = claim(maker, NOVA, "battery_life", value=11, unit="h", quote=table_text)
    proposal = fc.propose(one_candidate(), maker, [stated])
    assert proposal.words == "Battery life Up to 11 hours of video"
    a = hours(page("https://techdesk.example/nova"), 10, quote="Battery:\n10 hours")
    (one,) = candidates(found(a, hours(page("https://gadgets.example/nova"), 14)))
    rows = fc.candidate_rows(one, proposal, "c900", TABLE)
    for row in rows:
        assert not any(c in value for value in row.values() for c in "\n\r\t​")
    assert {r["claim_site"]: r["claim_quote"] for r in rows}["techdesk.example"] == "Battery: 10 hours"


def test_a_quote_is_cut_to_one_sentence_by_check_conflicts_rule() -> None:
    quote = 'Asus claims "up to 11 hours." We got 8 hours.'
    assert fc._words_around(quote, 8, "h", 200) == "We got 8 hours."
    assert fc._words_around(quote, 11, "h", 200) == 'Asus claims "up to 11 hours."'


def test_a_long_sentence_is_cut_around_the_figure_without_half_words() -> None:
    sentence = "In our long and careful test of the screen at full brightness the battery held on for 9 hours " + (
        "while we streamed video and browsed the web with many tabs open all day long"
    )
    words = fc._words_around(sentence, 9, "h", 60)
    assert words is not None and "9 hours" in words and len(words) <= 60 and words in sentence
    assert sentence.split().count(words.split()[0]) and sentence.split().count(words.split()[-1])


def test_a_very_long_maker_link_proposes_nothing_and_stays_within_300_characters() -> None:
    long_url = "https://nova.example/specs?" + "q=battery&" * 30
    maker = page(long_url, SourceType.MANUFACTURER, text="Battery life: up to 11 hours.")
    stated = claim(maker, NOVA, "battery_life", value=11, unit="h", quote="Battery life: up to 11 hours.")
    proposal = fc.propose(one_candidate(), maker, [stated])
    assert proposal.answer is None and proposal.why == fc.LINK_TOO_LONG
    assert len(fc.how_established(proposal)) <= 300
    assert fc.how_established(proposal).startswith("PROPOSED: no answer")


def test_claim_tier_is_the_one_verify_uses() -> None:
    lab = page("https://www.notebookcheck.net/Nova-Book-14-review.html")  # read as a review site; trust.yaml: lab
    (one,) = candidates(found(hours(lab, 10), hours(page("https://gadgets.example/nova"), 14)))
    tiers = {r["claim_site"]: r["claim_tier"] for r in fc.candidate_rows(one, fc.Proposal(), "c900", TABLE)}
    assert tiers == {"notebookcheck.net": "lab", "gadgets.example": "editorial"}


# --- the file ---------------------------------------------------------------------------------------------------------


def test_candidates_use_conflicts_csv_columns_marked_proposed_with_added_by_empty(tmp_path: Path) -> None:
    with fc.CONFLICTS.open(encoding="utf-8", newline="") as file:
        header = next(csv.reader(file))
    assert list(fc.HEADER) == header
    first = next(fc.next_conflict_ids(fc.CONFLICTS))
    taken = {row["conflict_id"] for row in csv.DictReader(fc.CONFLICTS.open(encoding="utf-8", newline=""))}
    assert first not in taken and int(first[1:]) > max(int(t[1:]) for t in taken if t.startswith("c"))

    rows = fc.candidate_rows(one_candidate(), fc.propose(one_candidate(), None, []), first, TABLE)
    assert len(rows) == 2
    for row in rows:
        assert list(row) == list(fc.HEADER)
        assert row["how_established"].startswith("PROPOSED")
        assert row["added_by"] == ""
        assert row["conflict_id"] == first
    by_site = {r["claim_site"]: r for r in rows}
    assert by_site["techdesk.example"]["claim_value"] == "10 h"
    assert by_site["techdesk.example"]["claim_tier"] == "editorial"
    assert by_site["techdesk.example"]["claim_quote"] == "Battery lasts 10 hours."
    out = tmp_path / "2026-11-14.csv"
    fc.write_candidates(out, rows)
    assert list(csv.DictReader(out.open(encoding="utf-8", newline=""))) == rows


def test_the_output_never_touches_conflicts_csv(tmp_path: Path) -> None:
    before = fc.CONFLICTS.read_bytes()
    rows = fc.candidate_rows(one_candidate(), fc.propose(one_candidate(), None, []), "c900", TABLE)
    fc.write_candidates(tmp_path / "out.csv", rows)
    for refused in (fc.CONFLICTS, fc.CONFLICTS.parent / "anything.csv"):
        with pytest.raises(ValueError, match="never writes into ml/labelling"):
            fc.write_candidates(refused, rows)
    assert fc.CONFLICTS.read_bytes() == before
    assert fc.default_out().parent == fc.OUT_DIR
    assert "interim" in fc.OUT_DIR.parts


def test_found_searches_become_a_candidates_file(tmp_path: Path) -> None:
    """The whole path after the searches, with a fake maker-page finder and a fake extractor."""
    maker = page(MAKER_URL, SourceType.MANUFACTURER, text="Battery life: up to 11 hours.")

    class Maker:
        def find(self, candidate: fc.Candidate, f: fc.Found) -> Source | None:
            return maker

    class Extractor:
        def claims(self, page_: Source, candidate: fc.Candidate) -> list[Claim]:
            return [claim(page_, NOVA, "battery_life", value=11, unit="h", quote="Battery life: up to 11 hours.")]

    f = found(hours(page("https://techdesk.example/nova"), 10), hours(page("https://gadgets.example/nova"), 14))
    out = tmp_path / "c.csv"
    count = fc.write_found([f], Maker(), Extractor(), out, TABLE, known=set(), ids=iter(["c900", "c901"]))
    rows = list(csv.DictReader(out.open(encoding="utf-8", newline="")))
    assert count == 1 and len(rows) == 2
    assert {r["known_answer"] for r in rows} == {"11 h"}
    assert {r["tolerance"] for r in rows} == {"0.55"}  # 5 % of 11 h, for the person to check


class _MakerPage:
    def find(self, candidate: fc.Candidate, f: fc.Found) -> Source | None:
        return page(MAKER_URL, SourceType.MANUFACTURER, text="Nova Book 14. Thin and light.")


class _Says:
    def __init__(self, *, fails: bool) -> None:
        self.fails = fails

    def claims(self, page_: Source, candidate: fc.Candidate) -> list[Claim]:
        if self.fails:
            raise fc.PageNotRead("1 source could not be read")
        return []


def test_a_maker_page_that_could_not_be_read_says_so_and_one_that_was_read_says_it_is_silent(tmp_path: Path) -> None:
    f = found(hours(page("https://techdesk.example/nova"), 10), hours(page("https://gadgets.example/nova"), 14))
    notes = {}
    for fails in (True, False):
        out = tmp_path / f"{fails}.csv"
        assert fc.write_found([f], _MakerPage(), _Says(fails=fails), out, TABLE, set(), iter(["c900"])) == 1
        rows = list(csv.DictReader(out.open(encoding="utf-8", newline="")))
        assert {r["known_answer"] for r in rows} == {""}
        (notes[fails],) = {r["how_established"] for r in rows}
    assert notes[True] == f"PROPOSED: no answer: the maker page {MAKER_URL} {fc.NOT_READ}."
    assert notes[False] == f"PROPOSED: no answer: the maker page {MAKER_URL} doesn't state it in h."


def test_ids_and_categories_read_a_file_saved_from_a_spreadsheet(tmp_path: Path) -> None:
    saved = tmp_path / "conflicts.csv"
    saved.write_bytes(b"\xef\xbb\xbf" + fc.CONFLICTS.read_bytes())  # Excel's "CSV UTF-8" starts with a byte-order mark
    assert next(fc.next_conflict_ids(saved)) == next(fc.next_conflict_ids(fc.CONFLICTS)) != "c001"
    assert fc.known_pairs(saved) == fc.known_pairs(fc.CONFLICTS) != set()
    listings = tmp_path / "listings.csv"
    listings.write_bytes(b"\xef\xbb\xbf" + fc.LISTINGS.read_bytes())
    assert fc.categories(listings) == fc.categories(fc.LISTINGS)


def test_a_second_run_the_same_day_gets_its_own_file_and_ids(tmp_path: Path) -> None:
    day = date(2026, 11, 14)
    first = fc.default_out(day, folder=tmp_path)
    rows = fc.candidate_rows(one_candidate(), fc.Proposal(), "c950", TABLE)
    fc.write_candidates(first, rows)
    with pytest.raises(FileExistsError):
        fc.write_candidates(first, rows)  # never overwritten
    second = fc.default_out(day, folder=tmp_path)
    assert (first.name, second.name) == ("2026-11-14.csv", "2026-11-14-2.csv")
    assert next(fc.next_conflict_ids(fc.CONFLICTS, first)) == "c951"


def test_an_out_file_in_ml_labelling_is_refused_before_any_search(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_search(category: str, query: str) -> fc.Found | None:
        raise AssertionError("a search ran")

    monkeypatch.setattr(fc, "run_search", no_search)
    before = fc.CONFLICTS.read_bytes()
    for out in (fc.CONFLICTS, fc.LABELLING / "candidates.csv"):
        with pytest.raises(SystemExit):
            fc.main(["--out", str(out)])
    assert fc.CONFLICTS.read_bytes() == before


def test_the_categories_come_from_listings_csv() -> None:
    found_categories = fc.categories(fc.LISTINGS)
    assert set(found_categories) == {"smartphone", "air_fryer", "laptop", "smartwatch", "earbuds", "headphones"}
    assert all(fc.QUERIES[c] for c in found_categories)


# --- the budget -------------------------------------------------------------------------------------------------------


def request(url: str) -> httpx.Request:
    return httpx.Request("POST", url)


def test_the_budget_refuses_a_call_past_its_share_and_reddit_always() -> None:
    budget = fc.Budget({"llm": 2, "serper": 1, "jina": 1, "firecrawl": 3})
    budget.admit(request("https://api.groq.com/v1/chat/completions"))
    budget.admit(request("https://api.mistral.ai/v1/chat/completions"))  # every provider counts as one budget
    with pytest.raises(fc.OverBudget):
        budget.admit(request("https://api.groq.com/v1/chat/completions"))
    with pytest.raises(fc.OverBudget):
        budget.admit(request("https://arctic-shift.photon-reddit.com/api/posts/search"))  # Reddit: always 0
    budget.admit(request("https://www.techdesk.example/review"))  # a plain page read: not budgeted
    budget.admit(request("https://api.firecrawl.dev/v2/search"))  # a search costs 2 credits
    with pytest.raises(fc.OverBudget):
        budget.admit(request("https://api.firecrawl.dev/v2/search"))
    budget.admit(request("https://api.firecrawl.dev/v2/scrape"))  # a scrape, 1
    with budget.share(serper=0), pytest.raises(fc.OverBudget):
        budget.admit(request("https://google.serper.dev/search"))
    budget.admit(request("https://google.serper.dev/search"))  # outside the share, the total still has one
    assert budget.used == {"llm": 2, "serper": 1, "jina": 0, "firecrawl": 3}


def test_the_guard_counts_every_httpx_call_and_is_undone(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []

    def answer(transport: httpx.HTTPTransport, req: httpx.Request) -> httpx.Response:
        sent.append(str(req.url))
        return httpx.Response(200, json={}, request=req)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", answer)
    budget = fc.Budget({"llm": 2, "serper": 0, "jina": 0, "firecrawl": 0})
    groq = "https://api.groq.com/v1/chat/completions"
    with budget.guarding(), httpx.Client() as client:
        client.post(groq)
        client.post(groq)
        with pytest.raises(fc.OverBudget):
            client.post(groq)
        with pytest.raises(fc.OverBudget):
            client.get("https://arctic-shift.photon-reddit.com/api/posts/search")
        client.get("https://www.techdesk.example/review")  # a plain page read
    assert sent == [groq, groq, "https://www.techdesk.example/review"]
    assert budget.used["llm"] == 2
    with httpx.Client() as client:
        client.post(groq)  # undone: no longer counted or refused
    assert budget.used["llm"] == 2 and len(sent) == 4


def _failed_request(cause: Exception) -> ProviderError:
    """As app/llm/providers.py turns a failed request into a provider error: `raise ... from None`."""
    try:
        try:
            raise cause
        except httpx.HTTPError as exc:
            raise ProviderError(Provider.GROQ, f"request failed ({type(exc).__name__})") from None
    except ProviderError as error:
        return error


def test_a_call_the_budget_refused_never_pauses_a_provider() -> None:
    refused = _failed_request(fc.OverBudget("the llm budget is spent", request=request("https://api.groq.com/")))
    assert fc.refused_by_budget(refused)
    health = ProviderHealth()
    with fc.Budget({"llm": 0}).guarding():
        for _ in range(5):
            assert health.record_failure(Provider.GROQ, refused, "a-model") is False
        assert health.state(Provider.GROQ, "a-model") != "cooling"
        dropped = _failed_request(httpx.ConnectError("no route", request=request("https://api.groq.com/")))
        assert not fc.refused_by_budget(dropped)
        for _ in range(3):  # a real failure still counts, three times pausing the provider
            health.record_failure(Provider.GROQ, dropped, "a-model")
        assert health.state(Provider.GROQ, "a-model") == "cooling"
