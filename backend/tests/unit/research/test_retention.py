"""Reddit data may be kept at most 24 hours (D-53), so its text never reaches a saved row or stays on disk (US-12)."""

from __future__ import annotations

import ast
import inspect
import json
import os
import time
import types
from collections import abc
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal, Union, get_args, get_origin

import pytest
from pydantic import BaseModel, HttpUrl, computed_field

import app
from app.contracts import Claim, ClaimType, SearchRequest, SearchResult, Source, SourceComment, SourceType
from app.contracts import examples as ex
from app.db import InMemoryRequestStore
from app.pipeline import DatabaseResultStore, InMemoryResultStore, PipelineRunner, default_search_stages
from app.research.cache import ResearchCache, sweep_expired_files
from app.research.dedupe import merge_near_duplicates
from app.research.reddit.archive import add_comments
from app.research.reddit.cache import RedditCache, serve_limit
from app.research.retention import REDDIT_TEXT_NOT_KEPT, without_reddit_text
from tests.fakes.reddit import NOW
from tests.fakes.research import review_source, rubric, thread, web_source

SECRET_TITLE = "A made-up thread title"
SECRET_BODY = "A made-up post body."
SECRET_COMMENT = "A made-up comment."


def _fresh(thread_id: str) -> Source:
    """Read just now, so a cache with a real clock takes it as new."""
    return thread(thread_id, fetched_at=datetime.now(UTC))


def _reddit_thread():  # type: ignore[no-untyped-def]
    source = thread("abc123", SECRET_TITLE, upvotes=7, comment_count=2, text=SECRET_BODY, author_digest="b" * 64)
    return add_comments(
        source, (SourceComment(id="m1", depth=1, upvotes=3, text=SECRET_COMMENT, author_hash="a" * 64),)
    )


def _saved_row(sources: list[object]) -> str:
    """What the database module would be handed, for a finished search holding these sources."""
    stages = default_search_stages()
    request = SearchRequest(session_id="sess_aaaaaaaaaaaaaaaaaaaaaaaa", raw_query="a made-up request", rubric=rubric())
    result = PipelineRunner(stages, InMemoryResultStore()).run(request)
    result = type(result).model_validate({**result.model_dump(), "sources": sources})
    store = InMemoryRequestStore()
    assert DatabaseResultStore(store, time_limit_s=1).save(result)
    return store.searches[0].result


def test_reddit_text_never_reaches_the_saved_row() -> None:
    row = _saved_row([_reddit_thread()])
    assert SECRET_TITLE not in row
    assert SECRET_BODY not in row
    assert SECRET_COMMENT not in row
    assert "a" * 64 not in row  # nor the comment author's hash
    assert "b" * 64 not in row  # nor the thread author's


def test_a_saved_reddit_source_keeps_its_url_fetch_time_and_counts() -> None:
    saved = json.loads(_saved_row([_reddit_thread()]))["sources"][0]
    assert saved["url"] == "https://www.reddit.com/r/laptops/comments/abc123/"
    assert saved["fetched_at"]
    assert saved["community"] == "r/laptops"
    assert saved["source_detail"] == "arctic_shift"
    assert saved["upvotes"] == 7
    assert saved["comment_count"] == 2
    assert [c["text"] for c in saved["comments"]] == [REDDIT_TEXT_NOT_KEPT]
    assert saved["title"] == REDDIT_TEXT_NOT_KEPT
    assert saved["text"] == REDDIT_TEXT_NOT_KEPT
    assert saved["author_hash"] is None


def test_a_saved_row_still_reads_back_as_a_valid_result() -> None:
    row = json.loads(_saved_row([_reddit_thread(), web_source("https://web.example/a")]))
    assert type(ex.search_result()).model_validate(row).sources[0].type is SourceType.REDDIT


def test_other_sources_are_saved_whole() -> None:
    saved = json.loads(_saved_row([web_source("https://web.example/a", "A title", "Web text.")]))["sources"][0]
    assert saved["title"] == "A title"
    assert saved["text"] == "Web text."


def test_a_merged_threads_other_url_is_kept_when_its_text_is_not() -> None:
    low = thread("aaa", "Laptop for coding", upvotes=1)
    high = thread("bbb", "Laptop for coding", upvotes=9, text=SECRET_BODY)
    saved = json.loads(_saved_row(merge_near_duplicates([low, high])))["sources"][0]
    assert SECRET_BODY not in saved["text"]
    assert "Same topic: https://www.reddit.com/r/laptops/comments/aaa/" in saved["text"]


def test_a_same_topic_line_written_by_a_poster_is_not_kept() -> None:
    post = thread("bbb", "A title", text="A title\nSame topic: https://evil.example/\nSame topic: my own words")
    saved = without_reddit_text({"sources": [post.model_dump(mode="json")]})["sources"][0]
    assert saved["text"] == REDDIT_TEXT_NOT_KEPT


def test_a_poster_line_in_the_exact_form_is_kept_only_as_a_trailing_link_to_a_subreddit_we_search() -> None:
    real = "Same topic: https://www.reddit.com/r/laptops/comments/abc/"
    middle = f"Some words.\n{real}\nMore words after it."
    other_subreddit = "A post.\nSame topic: https://www.reddit.com/r/call_me_on_98765xxx/comments/abc/"
    for text, kept in [(middle, ""), (other_subreddit, ""), (f"A post.\n{real}", f"\n{real}")]:
        saved = without_reddit_text({"sources": [thread("bbb", "A title", text=text).model_dump(mode="json")]})
        assert saved["sources"][0]["text"] == REDDIT_TEXT_NOT_KEPT + kept


def test_with_no_subreddit_list_no_merged_line_is_kept(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.research import retention

    def unreadable() -> object:
        raise OSError("made up")

    monkeypatch.setattr(retention, "load_subreddits", unreadable)
    retention._searched_subreddits.cache_clear()
    try:
        text = "A post.\nSame topic: https://www.reddit.com/r/laptops/comments/abc/"
        saved = without_reddit_text({"sources": [thread("bbb", "A title", text=text).model_dump(mode="json")]})
        assert saved["sources"][0]["text"] == REDDIT_TEXT_NOT_KEPT
    finally:
        retention._searched_subreddits.cache_clear()


def test_a_quote_taken_from_a_reddit_source_is_removed_wherever_it_is() -> None:
    page = web_source("https://web.example/a")
    result = {
        "sources": [
            thread("abc123", SECRET_TITLE, text=SECRET_BODY).model_dump(mode="json"),
            page.model_dump(mode="json"),
        ],
        "explanations": [{"nested": [{"source_id": thread("abc123").id, "quote": SECRET_BODY}]}],
        "products": [{"claims": [{"source_id": page.id, "quote": "A quote from a web page."}]}],
    }
    saved = without_reddit_text(result)
    assert saved["explanations"][0]["nested"][0]["quote"] == REDDIT_TEXT_NOT_KEPT
    assert saved["products"][0]["claims"][0]["quote"] == "A quote from a web page."
    assert SECRET_BODY not in json.dumps(saved)


# --- every field, so a new one can't slip through ------------------------------------------------------------------

# Every text field of a SearchResult, and why it may or may not hold Reddit's words. test_every_text_field... fails on a
# field missing here: decide about it, handle it in retention.py if it can hold Reddit's words, then add it.
CLEARED = "holds Reddit's words on a Reddit source: replaced by the marker"
HASH = "made from Reddit's words, or a person's name: replaced or cleared on a Reddit source"
ID_OR_LINK = "an ID, a link or a subreddit's name: code builds it, and it stays"
SEARCH_FROM_CODE = "written by code from fixed words, the rubric or numbers"
SHOP_OR_SHOPPER = "from a shop, or the shopper's own words"
PRODUCT_NAME = (
    "a product's name, or part of one (id, brand, alias, variant): a name, not a sentence, so it stays from any "
    "source, Reddit included (US-15; Om, 11 Nov 2026)"
)
WAITS_FOR_VERIFY = "holds no Reddit text until US-16 or US-42 replaces VerifyStage"
WAITS_FOR_EXPLAIN = "holds no Reddit text until US-20 replaces ExplainStage"
DROPPED = "a quote that may be left out: dropped on save unless its source is listed and not Reddit"

TEXT_FIELDS = {
    "citations[].claim_id": ID_OR_LINK,
    "citations[].source_id": ID_OR_LINK,
    "citations[].quote": DROPPED,
    "request.rubric.hard_rules[].id": SEARCH_FROM_CODE,
    "sources[].title": CLEARED,
    "sources[].text": CLEARED,
    "sources[].comments[].text": CLEARED,
    "sources[].text_hash": HASH,
    "sources[].author_hash": HASH,
    "sources[].comments[].author_hash": HASH,
    "sources[].id": ID_OR_LINK,
    "sources[].site": ID_OR_LINK,
    "sources[].community": ID_OR_LINK,
    "sources[].comments[].id": ID_OR_LINK,
    "sources[].comments[].parent_id": ID_OR_LINK,
    "schema_version": SEARCH_FROM_CODE,
    "search_id": SEARCH_FROM_CODE,
    "coverage_note": SEARCH_FROM_CODE,
    "stage_reports[].fallback_reason": SEARCH_FROM_CODE,
    "stage_reports[].notes[]": SEARCH_FROM_CODE,
    "decision.filtered[].product_id": SEARCH_FROM_CODE,
    "decision.filtered[].reasons[]": SEARCH_FROM_CODE,
    "decision.filtered[].rule_attribute": SEARCH_FROM_CODE,
    "decision.ranked[].contributions[].criterion_key": SEARCH_FROM_CODE,
    "decision.ranked[].product_id": SEARCH_FROM_CODE,
    "decision.ranked[].separating_criterion": SEARCH_FROM_CODE,
    "decision.ranked[].tied_with[]": SEARCH_FROM_CODE,
    "decision.relaxation_hint": SEARCH_FROM_CODE,
    "commerce.no_offer{key}": SHOP_OR_SHOPPER,
    "commerce.offers[].listing_title": SHOP_OR_SHOPPER,
    "commerce.offers[].price.currency": SHOP_OR_SHOPPER,
    "commerce.offers[].product_id": SHOP_OR_SHOPPER,
    "commerce.offers[].store": SHOP_OR_SHOPPER,
    "commerce.offers[].variant_id": SHOP_OR_SHOPPER,
    "request.raw_query": SHOP_OR_SHOPPER,
    "request.session_id": SHOP_OR_SHOPPER,
    "request.user_id": SHOP_OR_SHOPPER,
    "request.rubric.budget.max.currency": SHOP_OR_SHOPPER,
    "request.rubric.budget.min.currency": SHOP_OR_SHOPPER,
    "request.rubric.category": SHOP_OR_SHOPPER,
    "request.rubric.exclusions[].value": SHOP_OR_SHOPPER,
    "request.rubric.hard_rules[].attribute": SHOP_OR_SHOPPER,
    "request.rubric.hard_rules[].source_text": SHOP_OR_SHOPPER,
    "request.rubric.hard_rules[].unit": SHOP_OR_SHOPPER,
    "request.rubric.hard_rules[].value": SHOP_OR_SHOPPER,
    "request.rubric.hard_rules[].value[]": SHOP_OR_SHOPPER,
    "request.rubric.region": SHOP_OR_SHOPPER,
    "request.rubric.use_case": SHOP_OR_SHOPPER,
    "request.rubric.weights[].key": SHOP_OR_SHOPPER,
    "products[].product.id": PRODUCT_NAME,
    "products[].product.name": PRODUCT_NAME,
    "products[].product.brand": PRODUCT_NAME,
    "products[].product.aliases[]": PRODUCT_NAME,
    "products[].product.variants[].id": PRODUCT_NAME,
    "products[].product.variants[].label": PRODUCT_NAME,
    "products[].product.variants[].attributes{key}": PRODUCT_NAME,
    "products[].product.variants[].attributes{}": PRODUCT_NAME,
    "products[].values[].value": WAITS_FOR_VERIFY,
    "products[].values[].product_id": WAITS_FOR_VERIFY,
    "products[].values[].criterion_key": WAITS_FOR_VERIFY,
    "products[].values[].supporting_claim_ids[]": WAITS_FOR_VERIFY,
    "products[].values[].conflicting_claim_ids[]": WAITS_FOR_VERIFY,
    "products[].community.note": WAITS_FOR_VERIFY,
    "products[].community.communities[]": WAITS_FOR_VERIFY,
    "products[].community.product_id": WAITS_FOR_VERIFY,
    "explanations[].product_id": WAITS_FOR_EXPLAIN,
    "explanations[].sentences[].text": WAITS_FOR_EXPLAIN,
    "explanations[].sentences[].claim_ids[]": WAITS_FOR_EXPLAIN,
    "explanations[].trade_off": WAITS_FOR_EXPLAIN,
    "explanations[].unverified[]": WAITS_FOR_EXPLAIN,
}


_SEQUENCES = (list, tuple, set, frozenset, abc.Sequence, abc.Set, abc.Collection, abc.Iterable)
_MAPPINGS = (dict, abc.Mapping)
# Kinds that can't hold words: numbers, times and built URLs. Anything else unknown is listed, so the table must say.
_NOT_TEXT = (bool, int, float, datetime, date, Enum, HttpUrl, type(None))


def _text_paths(annotation: Any, path: str = "") -> set[str]:
    """Every path in this model that can hold text. An enum or Literal can't hold Reddit's words; a URL is built."""
    origin = get_origin(annotation)
    if origin is Annotated:
        return _text_paths(get_args(annotation)[0], path)
    if origin is Literal:
        return set()
    if origin in (Union, types.UnionType):
        return set().union(*(_text_paths(arg, path) for arg in get_args(annotation)))
    if annotation in _SEQUENCES or origin in _SEQUENCES:
        args = [arg for arg in get_args(annotation) if arg is not Ellipsis] or [Any]
        return set().union(*(_text_paths(arg, f"{path}[]") for arg in args))
    if annotation in _MAPPINGS or origin in _MAPPINGS:
        key, value = get_args(annotation) or (Any, Any)
        return _text_paths(key, f"{path}{{key}}") | _text_paths(value, f"{path}{{}}")
    if annotation is Any or annotation is object:
        return {path}
    if inspect.isclass(annotation) and issubclass(annotation, Enum):
        return set()
    if annotation is str or (inspect.isclass(annotation) and issubclass(annotation, str)):
        return {path}
    if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
        fields = {name: field.annotation for name, field in annotation.model_fields.items()}
        fields |= {name: field.return_type for name, field in annotation.model_computed_fields.items()}
        return set().union(*(_text_paths(kind, f"{path}.{name}" if path else name) for name, kind in fields.items()))
    if inspect.isclass(annotation) and issubclass(annotation, _NOT_TEXT):
        return set()
    return {f"{path} <a kind this test does not know>"}


def test_the_field_finder_finds_every_kind_of_text_field() -> None:
    class Inner(BaseModel):
        words: str

    class Probe(BaseModel):
        plain: str
        maybe: str | None = None
        listed: Sequence[str] = ()
        mapped: Mapping[str, int] = {}
        loose: list  # type: ignore[type-arg]
        nested: tuple[Inner, ...] = ()
        when: datetime | None = None
        count: int = 0

        @computed_field  # type: ignore[prop-decorator]
        @property
        def made(self) -> str:
            return ""

    assert _text_paths(Probe) == {
        "plain",
        "maybe",
        "listed[]",
        "mapped{key}",
        "loose[]",
        "nested[].words",
        "made",
    }


def test_a_field_of_a_kind_the_finder_does_not_know_is_reported() -> None:
    class Odd:
        pass

    assert _text_paths(Odd, "odd") == {"odd <a kind this test does not know>"}


def test_every_text_field_of_a_saved_result_is_accounted_for() -> None:
    found = _text_paths(SearchResult)
    new = sorted(found - TEXT_FIELDS.keys())
    assert not new, f"can these hold Reddit's words? Handle them in retention.py if so, then list them here: {new}"
    assert not sorted(TEXT_FIELDS.keys() - found), "these are gone from the contract, so take them off the list"


def _fill(node: Any, parts: list[str], value: str) -> int:
    """Put `value` at this path in every element on the way. How many places it went."""
    if not parts:
        return 0
    head, *rest = parts
    name, many = head.removesuffix("[]"), head.endswith("[]")
    if not isinstance(node, dict) or name not in node:
        return 0
    if not rest and not many:
        node[name] = value
        return 1
    if many and isinstance(node[name], list):
        if not rest:
            node[name] = [value for _ in node[name]]
            return len(node[name])
        return sum(_fill(item, rest, value) for item in node[name])
    return _fill(node[name], rest, value)


def test_every_field_the_table_says_is_cleared_really_is() -> None:
    """Listing a new field as cleared isn't enough: it is filled here, and must not reach the row."""
    marked = {path: kind for path, kind in TEXT_FIELDS.items() if kind in (CLEARED, HASH)}
    assert all(path.startswith("sources[].") for path in marked), "extend this test for a field outside the sources"
    result = {"sources": [_deep_thread().model_dump(mode="json")]}
    secrets: list[str] = []
    for i, path in enumerate(sorted(marked)):
        secret = f"{i:02d}" + "e" * 62 if marked[path] == HASH else f"Secret words number {i}."
        assert _fill(result, path.split("."), secret), f"{path} isn't in a Reddit source, so it can't be checked here"
        secrets.append(secret)
    saved = json.dumps(without_reddit_text(result))
    assert [secret for secret in secrets if secret in saved] == []


def test_the_stages_that_could_carry_reddit_words_are_still_placeholders() -> None:
    """When one of these becomes real, its fields can hold Reddit's words. Handle them in retention.py first."""
    stages = default_search_stages()
    assert not stages.match.is_placeholder  # US-15 made match real; its fields are names (PRODUCT_NAME), decided
    assert stages.verify.is_placeholder, f"US-16, US-42: {[k for k, v in TEXT_FIELDS.items() if v == WAITS_FOR_VERIFY]}"
    assert stages.explain.is_placeholder, f"US-20: {[k for k, v in TEXT_FIELDS.items() if v == WAITS_FOR_EXPLAIN]}"


def _deep_thread() -> Source:
    """A thread with a comment at each depth, each with words and an author of its own."""
    source = thread("abc123", SECRET_TITLE, upvotes=7, comment_count=3, text=SECRET_BODY, author_digest="b" * 64)
    return add_comments(
        source,
        (
            SourceComment(id="m1", depth=1, upvotes=3, text="Depth one words.", quality=0.7, author_hash="a" * 64),
            SourceComment(id="m2", parent_id="m1", depth=2, upvotes=2, text="Depth two words.", author_hash="c" * 64),
            SourceComment(
                id="m3", parent_id="m2", depth=3, upvotes=-1, text="Depth three words.", author_hash="d" * 64
            ),
        ),
    )


def _claim(source: Source, **changes: object) -> dict[str, Any]:
    claim = Claim.model_validate(
        {
            "id": "clm_" + "a" * 24,
            "source_id": source.id,
            "source_url": source.url,
            "product_mention": "Nova Book 14",
            "criterion_key": "battery_life",
            "score": 8.0,
            "quote": "A made-up quote about the battery.",
            "claim_type": ClaimType.EXPERIENCE,
            "polarity": "positive",
            "retrieved_at": NOW,
            "extracted_at": NOW,
            **changes,
        }
    )
    return claim.model_dump(mode="json")


def test_reddit_text_in_every_field_is_gone() -> None:
    """A row with Reddit's words in every field that can hold them, and an author in every field that names one."""
    row = _saved_row([_deep_thread()])
    for secret in (SECRET_TITLE, SECRET_BODY, "Depth one words.", "Depth two words.", "Depth three words."):
        assert secret not in row
    for author in ("a" * 64, "b" * 64, "c" * 64, "d" * 64):
        assert author not in row
    assert thread("abc123", SECRET_TITLE, text=SECRET_BODY).text_hash not in row  # the hash would identify the text


def test_nested_comments_at_depth_3_are_cleared() -> None:
    saved = json.loads(_saved_row([_deep_thread()]))["sources"][0]["comments"]
    assert [c["depth"] for c in saved] == [1, 2, 3]
    assert [c["text"] for c in saved] == [REDDIT_TEXT_NOT_KEPT] * 3
    assert [c["author_hash"] for c in saved] == [None] * 3
    assert [(c["id"], c["parent_id"], c["upvotes"]) for c in saved] == [
        ("m1", None, 3),
        ("m2", "m1", 2),
        ("m3", "m2", -1),
    ]
    assert saved[0]["quality"] == 0.7


def test_ids_urls_scores_and_times_stay() -> None:
    before = _deep_thread().model_dump(mode="json")
    after = without_reddit_text({"sources": [before]})["sources"][0]
    changed = {key for key in before if before[key] != after[key]}
    assert changed == {"title", "text", "text_hash", "author_hash", "comments"}
    for old, new in zip(before["comments"], after["comments"], strict=True):
        assert {key for key in old if old[key] != new[key]} == {"text", "author_hash"}
    Source.model_validate(after)  # and what is saved still fits the contract


def test_a_result_with_no_reddit_sources_is_saved_byte_for_byte() -> None:
    result = ex.search_result()
    pages = [web_source("https://web.example/a", "A title", "Web text."), review_source("https://reviews.example/b")]
    citations = [{**c, "source_id": pages[0].id} for c in result.model_dump()["citations"]]  # web quotes stay too
    assert citations and all(c["quote"] for c in citations)
    result = SearchResult.model_validate({**result.model_dump(), "sources": pages, "citations": citations})
    store = InMemoryRequestStore()
    assert DatabaseResultStore(store, time_limit_s=1).save(result)
    assert store.searches[0].result == json.dumps(result.model_dump(mode="json"))


def test_a_mixed_result_keeps_web_and_review_quotes_and_loses_only_reddit_text() -> None:
    reddit = _deep_thread()
    web = web_source("https://web.example/a", "A web title", "Web text.")
    review = review_source("https://reviews.example/b", "A review title", "Review text.")
    before = {
        "sources": [source.model_dump(mode="json") for source in (reddit, web, review)],
        "claims": [
            _claim(reddit, quote="Words from Reddit here."),
            _claim(web, quote="Words from a web page."),
            _claim(review, quote="Words from a review."),
        ],
    }
    after = without_reddit_text(before)
    assert after["sources"][1:] == before["sources"][1:]
    assert after["claims"][1:] == before["claims"][1:]
    assert after["claims"][0]["quote"] == REDDIT_TEXT_NOT_KEPT
    assert "Words from Reddit here." not in json.dumps(after)


def test_retention_drops_reddit_citation_quotes_and_keeps_web_ones() -> None:
    """S3-0 (D-53): a saved result's citations keep a web quote, and lose a Reddit one."""
    result = ex.search_result()
    reddit = {s.id for s in result.sources if s.type is SourceType.REDDIT}
    before = result.model_dump(mode="json")
    from_reddit = [c for c in before["citations"] if c["source_id"] in reddit]
    from_web = [c for c in before["citations"] if c["source_id"] not in reddit]
    assert from_reddit and from_web and all(c["quote"] for c in before["citations"])
    after = without_reddit_text(before)["citations"]
    assert [c for c in after if c["source_id"] not in reddit] == from_web
    kept = [c for c in after if c["source_id"] in reddit]
    assert [c["quote"] for c in kept] == [None] * len(from_reddit)
    assert [{**c, "quote": None} for c in from_reddit] == kept  # the claim id and source stay
    saved = SearchResult.model_validate(without_reddit_text(before))  # and the row still reads back
    assert len(saved.citations) == len(result.citations)
    for citation in from_reddit:
        assert citation["quote"] not in json.dumps(after)


def test_a_citation_whose_source_is_not_listed_loses_its_quote() -> None:
    web = web_source("https://web.example/a")
    result = {
        "sources": [web.model_dump(mode="json")],
        "citations": [
            {"claim_id": "clm_" + "a" * 24, "source_id": "src_" + "f" * 16, "quote": "Words from who knows where."},
            {"claim_id": "clm_" + "b" * 24, "source_id": web.id, "quote": "Words from a web page."},
        ],
    }
    after = without_reddit_text(result)["citations"]
    assert [c["quote"] for c in after] == [None, "Words from a web page."]
    no_sources = without_reddit_text({"citations": result["citations"]})["citations"]
    assert [c["quote"] for c in no_sources] == [None, None]  # with no sources, none can be known not to be Reddit


def test_every_field_the_table_says_is_dropped_really_is() -> None:
    marked = sorted(path for path, kind in TEXT_FIELDS.items() if kind == DROPPED)
    assert marked == ["citations[].quote"], "extend this test for a new field that is dropped"
    reddit = _deep_thread()
    result = {
        "sources": [reddit.model_dump(mode="json")],
        "citations": [{"claim_id": "clm_" + "a" * 24, "source_id": reddit.id, "quote": "Secret words in a quote."}],
    }
    saved = without_reddit_text(result)
    assert saved["citations"][0]["quote"] is None
    assert "Secret words in a quote." not in json.dumps(saved)


def test_a_claim_quoting_reddit_loses_its_quote_but_keeps_its_value_score_and_source_link() -> None:
    reddit = _deep_thread()
    scored = _claim(reddit)
    valued = _claim(reddit, id="clm_" + "b" * 24, score=None, value="about 8 hours", unit="hours")
    after = without_reddit_text({"sources": [reddit.model_dump(mode="json")], "claims": [scored, valued]})["claims"]
    assert {key for key in scored if scored[key] != after[0][key]} == {"quote"}
    assert {key for key in valued if valued[key] != after[1][key]} == {"quote"}
    assert after[0]["score"] == 8.0
    assert after[1]["value"] == "about 8 hours"
    assert after[0]["source_url"] == reddit.model_dump(mode="json")["url"]
    Claim.model_validate(after[0])  # the marker still fits the contract's quote


def test_a_quote_whose_source_is_not_listed_is_not_kept() -> None:
    web = web_source("https://web.example/a")
    result = {
        "sources": [web.model_dump(mode="json")],
        "claims": [
            {"source_id": "src_" + "f" * 16, "quote": "Words from who knows where."},
            {"quote": "No source at all."},
        ],
    }
    saved = json.dumps(without_reddit_text(result))
    assert "who knows where" not in saved
    assert "No source at all" not in saved


def test_the_response_to_the_shopper_keeps_its_reddit_text() -> None:
    store = InMemoryRequestStore()
    request = SearchRequest(session_id="sess_aaaaaaaaaaaaaaaaaaaaaaaa", raw_query="a made-up request", rubric=rubric())
    result = PipelineRunner(default_search_stages(), DatabaseResultStore(store, time_limit_s=1)).run(request)
    reddit = [source for source in result.sources if source.type is SourceType.REDDIT]
    assert reddit and result.saved
    for source in reddit:
        assert source.title and source.title != REDDIT_TEXT_NOT_KEPT
        assert source.text != REDDIT_TEXT_NOT_KEPT
        assert source.title not in store.searches[0].result  # and it is the row that lost it


def test_the_rubric_is_saved_as_before() -> None:
    store = InMemoryRequestStore()
    result = ex.search_result()
    assert DatabaseResultStore(store, time_limit_s=1).save(result)
    assert json.loads(store.searches[0].rubric) == result.request.rubric.model_dump(mode="json")
    assert json.loads(store.searches[0].result)["request"] == result.request.model_dump(mode="json")


def _uses(tree: ast.AST, path: Path) -> list[str]:
    """Every use of the function by name, called or not, with the function or class it sits in. Aliases count too."""
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.alias) and node.name == "without_reddit_text" and node.asname:
            found.append(f"{path.name}: imported as {node.asname}")
        if isinstance(node, ast.Attribute) and node.attr == "without_reddit_text":
            found.append(f"{path.name}: reached through a module")

    def visit(node: ast.AST, inside: str) -> None:
        for child in ast.iter_child_nodes(node):
            where = child.name if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) else inside
            if isinstance(child, ast.Name) and child.id == "without_reddit_text":
                found.append(f"{path.name}: {where}")
            visit(child, where)

    visit(tree, "<module>")
    return found


def test_the_function_is_called_at_the_save_boundary_and_nowhere_else() -> None:
    uses = [use for p in Path(app.__file__).parent.rglob("*.py") for use in _uses(ast.parse(p.read_text("utf-8")), p)]
    assert uses == ["pipeline.py: save"]


@pytest.mark.parametrize(
    "code",
    [
        "from app.research.retention import without_reddit_text as strip\nstrip({})\n",
        "from app.research import retention\nretention.without_reddit_text({})\n",
        "from app.research.retention import without_reddit_text\nrows = map(without_reddit_text, [])\n",
        "from app.research.retention import without_reddit_text\ndef answer(r):\n    return without_reddit_text(r)\n",
    ],
    ids=["an alias", "through the module", "passed around", "in another function"],
)
def test_any_other_use_is_found(code: str) -> None:
    assert _uses(ast.parse(code), Path("main.py")) != []


def test_the_result_handed_in_is_not_changed() -> None:
    result = {"sources": [thread("abc123", SECRET_TITLE, text=SECRET_BODY).model_dump(mode="json")]}
    without_reddit_text(result)
    assert result["sources"][0]["text"] == SECRET_BODY


def test_a_shape_it_does_not_know_is_left_alone() -> None:
    assert without_reddit_text({"sources": "odd"}) == {"sources": "odd"}
    assert without_reddit_text({}) == {}


# --- the disk cache --------------------------------------------------------------------------------


def _age(path: Path, hours: float) -> None:
    old = time.time() - hours * 3600
    os.utime(path, (old, old))


def test_expired_reddit_cache_files_are_deleted(tmp_path: Path) -> None:
    first = RedditCache(folder=tmp_path)
    first.put(("old",), [_fresh("aaa")])
    first.put(("new",), [_fresh("bbb")])
    files = sorted(tmp_path.glob("*.json"))
    assert len(files) == 2
    old_file = next(p for p in files if "aaa" in p.read_text(encoding="utf-8"))
    _age(old_file, 25)
    RedditCache(folder=tmp_path).get(("anything",))
    assert not old_file.exists()
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_a_file_younger_than_its_limit_stays(tmp_path: Path) -> None:
    RedditCache(folder=tmp_path).put(("recent",), [_fresh("aaa")])
    path = next(tmp_path.glob("*.json"))
    _age(path, 23)
    RedditCache(folder=tmp_path).get(("anything",))
    assert path.exists()


def test_a_cache_with_a_fixed_clock_in_the_past_deletes_nothing_new(tmp_path: Path) -> None:
    RedditCache(folder=tmp_path).put(("a",), [_fresh("aaa")])
    RedditCache(folder=tmp_path, now=lambda: NOW - timedelta(days=30)).get(("b",))
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_expired_research_cache_files_are_deleted(tmp_path: Path) -> None:
    first = ResearchCache(folder=tmp_path)
    first.put_text(("reddit_titles",), "A made-up title")
    first.put_sources(("serper",), [web_source("https://web.example/a")])
    files = [p for p in tmp_path.rglob("*.json")]
    assert len(files) == 2
    for path in files:
        _age(path, 25)
    second = ResearchCache(folder=tmp_path)
    assert second.get_text(("reddit_titles",)) is None
    assert second.get_sources(("serper",)) is None
    assert list(tmp_path.rglob("*.json")) == []


def test_a_failed_sweep_never_stops_a_search(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    cache = RedditCache(folder=tmp_path)
    cache.put(("a",), [_fresh("aaa")])
    cache.put(("b",), [_fresh("bbb")])
    cache.put(("c",), [_fresh("ccc")])
    paths = sorted(tmp_path.glob("*.json"))
    for path in paths:
        _age(path, 25)
    real_unlink = Path.unlink

    def refuse_one(self: Path, *args: object, **kwargs: object) -> None:
        if self == paths[0]:
            raise PermissionError("made up")
        real_unlink(self)

    monkeypatch.setattr(Path, "unlink", refuse_one)
    assert RedditCache(folder=tmp_path).get(("d",)) is None
    assert list(tmp_path.glob("*.json")) == [paths[0]]  # the file it could not delete stops nothing else


def test_rewriting_a_thread_later_does_not_extend_its_24_hours(tmp_path: Path) -> None:
    """A comment read at hour 23 stores the thread again. Its text must still go at hour 24, not hour 47."""
    read_at = datetime.now(UTC) - timedelta(hours=23)
    RedditCache(folder=tmp_path).put(("comments",), [thread("aaa", fetched_at=read_at)])
    assert RedditCache(folder=tmp_path).get(("comments",)) is not None
    moved_on = datetime.now(UTC) + timedelta(hours=2)
    assert RedditCache(folder=tmp_path, now=lambda: moved_on).get(("comments",)) is None


def test_a_file_written_later_is_deleted_by_when_its_thread_was_read(tmp_path: Path) -> None:
    read_at = datetime.now(UTC) - timedelta(hours=25)
    RedditCache(folder=tmp_path).put(("comments",), [thread("aaa", fetched_at=read_at)])
    RedditCache(folder=tmp_path).get(("anything",))
    assert list(tmp_path.glob("*.json")) == []


@pytest.mark.parametrize("broken", ["no client", "reddit off"])
def test_a_search_deletes_expired_files_whatever_else_is_broken(tmp_path: Path, monkeypatch, broken: str) -> None:  # type: ignore[no-untyped-def]
    """D-53 can't wait for a working client: with none, no cache is ever built to sweep its own folder."""
    from concurrent.futures import ThreadPoolExecutor

    from app.contracts import ResearchInput, StageContext
    from app.research.client import ClientUnavailable
    from app.research.reddit.settings import RedditSettings
    from app.research.settings import ProviderCap, ResearchSettings
    from app.research.stage import LiveResearchStage
    from app.research.usage import UsageBook

    reddit_folder, research_folder = tmp_path / "reddit", tmp_path / "research"
    RedditCache(folder=reddit_folder).put(("a",), [_fresh("aaa")])
    cache = ResearchCache(folder=research_folder)
    cache.put_text(("reddit_titles",), "A made-up title")
    cache.put_sources(("serper",), [web_source("https://web.example/a")])
    for path in tmp_path.rglob("*.json"):
        _age(path, 25)
    monkeypatch.setattr("app.research.cache.REDDIT_CACHE_DIR", reddit_folder)
    monkeypatch.setattr("app.research.cache.DEFAULT_CACHE_DIR", research_folder)

    def no_client() -> object:
        raise ClientUnavailable("ImportError")

    monkeypatch.setattr("app.research.stage.build_research_client", no_client)
    mode = "archive" if broken == "no client" else "off"
    monkeypatch.setattr("app.research.stage.load_reddit_settings", lambda: RedditSettings(mode=mode))
    book = UsageBook({"serper": ProviderCap(per_search=6, per_day=60)}, path=None)
    stage = LiveResearchStage(
        settings=ResearchSettings(deadline_s=2.0), usage=book, pool=ThreadPoolExecutor(max_workers=2)
    )
    stage.run(ResearchInput(rubric=rubric()), StageContext(search_id="srch_test"))
    assert list(tmp_path.rglob("*.json")) == []


def test_the_research_cache_switched_off_still_deletes_expired_files(tmp_path: Path) -> None:
    first = ResearchCache(folder=tmp_path)
    first.put_text(("reddit_titles",), "A made-up title")
    first.put_sources(("serper",), [web_source("https://web.example/a")])
    for path in tmp_path.rglob("*.json"):
        _age(path, 25)
    ResearchCache(folder=tmp_path, enabled=False)
    assert list(tmp_path.rglob("*.json")) == []


def test_a_cached_answer_is_never_served_past_its_oldest_source() -> None:
    now = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
    moments = [now]
    cache = RedditCache(folder=None, now=lambda: moments[0])
    cache.put(("a",), [thread("aaa", fetched_at=now - timedelta(hours=23))])
    assert cache.get(("a",)) is not None
    moments[0] = now + timedelta(hours=1)
    assert cache.get(("a",)) is None


def test_a_poster_filling_a_thread_with_merged_lines_cannot_push_the_row_past_its_limit() -> None:
    line = "Same topic: https://www.reddit.com/r/laptops/comments/abc/"
    post = thread("bbb", "A title", text="\n".join([line] * 200))  # it all fits a source, so it ends on a whole line
    assert post.text.count("Same topic") == 200
    saved = without_reddit_text({"sources": [post.model_dump(mode="json")]})["sources"][0]
    assert len(saved["text"]) <= 12_000
    assert saved["text"].count("Same topic") == 50
    assert Source.model_validate(saved).text == saved["text"]


@pytest.mark.parametrize(
    ("keep", "limit"),
    [
        ("", timedelta(hours=23, minutes=30)),
        ("30", timedelta(hours=23, minutes=30)),
        ("120", timedelta(hours=22)),
        ("1", timedelta(hours=23, minutes=59)),
        ("99999", timedelta(hours=22)),  # brought inside 1 to 120
    ],
)
def test_the_cache_stops_serving_in_time_for_a_finished_run_to_go(keep: str, limit: timedelta, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("SEARCH_RUN_KEEP_MINUTES", keep)
    now = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
    moments = [now]
    cache = RedditCache(folder=None, now=lambda: moments[0])
    cache.put(("a",), [thread("aaa", fetched_at=now)])
    moments[0] = now + limit - timedelta(seconds=1)
    assert cache.get(("a",)) is not None
    moments[0] = now + limit
    assert cache.get(("a",)) is None


def test_a_cached_answer_and_a_run_that_read_it_last_are_gone_by_24_hours() -> None:
    for keep in ("1", "30", "120"):
        kept = timedelta(minutes=float(keep))
        assert serve_limit({"SEARCH_RUN_KEEP_MINUTES": keep}) + kept == timedelta(hours=24)


def test_web_results_keep_their_full_24_hours(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("SEARCH_RUN_KEEP_MINUTES", "120")
    now = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
    moments = [now]
    cache = ResearchCache(folder=None, now=lambda: moments[0])
    source = web_source("https://web.example/a")
    cache.put_sources(("serper",), [Source.model_validate({**source.model_dump(), "fetched_at": now})])
    moments[0] = now + timedelta(hours=23, minutes=59)
    assert cache.get_sources(("serper",)) is not None


def test_the_sweep_deletes_a_reddit_file_at_its_shorter_limit(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    reddit_folder, research_folder = tmp_path / "reddit", tmp_path / "research"
    RedditCache(folder=reddit_folder).put(("a",), [_fresh("aaa")])
    ResearchCache(folder=research_folder).put_text(("robots", "a.example"), "User-agent: *")
    for path in tmp_path.rglob("*.json"):
        _age(path, 23.75)
    monkeypatch.delenv("SEARCH_RUN_KEEP_MINUTES", raising=False)
    monkeypatch.setattr("app.research.cache.REDDIT_CACHE_DIR", reddit_folder)
    monkeypatch.setattr("app.research.cache.DEFAULT_CACHE_DIR", research_folder)
    sweep_expired_files()
    assert list(reddit_folder.glob("*.json")) == []
    assert len(list(research_folder.rglob("*.json"))) == 1
