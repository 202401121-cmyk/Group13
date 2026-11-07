"""Sprint 2 contracts (P-03): sources, claims, recommender counts, rough answers and search runs.

These are agreed and merged before the stories that use them, so every rule here is one a story relies on.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypeVar

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

import app.contracts as C
from app.contracts import examples as ex
from app.contracts.pipeline import MAX_RUN_EVENTS
from app.interview.answers import REPLY_WEIGHTS
from app.main import create_app
from app.research.reddit.base import make_source
from tests.contract.schema_check import schema_errors

M = TypeVar("M", bound=BaseModel)

SHARED = Path(__file__).resolve().parents[3] / "shared" / "contracts"


def rebuild(model: M, **changes: object) -> M:
    """Copy a model with changes AND re-run validation (model_copy would skip it)."""
    return type(model).model_validate({**model.model_dump(), **changes})


def exported(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((SHARED / "schema" / f"{name}.json").read_text(encoding="utf-8"))
    return data


def _web_source() -> C.Source:
    return next(s for s in ex.sources() if s.type is C.SourceType.REVIEW_SITE)


def _comment(**changes: object) -> dict[str, object]:
    return {**ex.reddit_thread().comments[0].model_dump(), **changes}


# --- Source (US-12, US-13, US-46) ------------------------------------------------------------------


def test_a_reddit_thread_keeps_its_votes_and_comments() -> None:
    thread = ex.reddit_thread()
    assert thread.type is C.SourceType.REDDIT
    assert thread.upvotes is not None and thread.comment_count is not None
    assert {c.depth for c in thread.comments} == {1, 2, 3}
    assert thread.community == "r/developersIndia"


def test_a_web_page_names_its_site() -> None:
    assert _web_source().site == "example-reviews.in"
    with pytest.raises(ValidationError, match="site or a community"):
        rebuild(_web_source(), site=None)


def test_a_source_has_a_site_or_a_community_not_both() -> None:
    with pytest.raises(ValidationError, match="site or a community"):
        rebuild(_web_source(), community="r/laptops")
    with pytest.raises(ValidationError, match="site or a community"):
        rebuild(ex.reddit_thread(), site="reddit.com")


@pytest.mark.parametrize("site", ["", "   ", "x" * 121])
def test_a_site_cannot_be_blank_or_too_long(site: str) -> None:
    with pytest.raises(ValidationError):
        rebuild(_web_source(), site=site)


def test_a_community_cannot_be_blank() -> None:
    with pytest.raises(ValidationError):
        rebuild(_web_source(), site=None, community="")


def test_data_class_follows_the_source_type_when_left_out() -> None:
    reddit, review, _ = ex.sources()
    assert reddit.data_class is C.DataClass.REDDIT
    assert review.data_class is C.DataClass.PUBLIC


def test_a_source_data_class_must_agree_with_its_type() -> None:
    reddit, review, _ = ex.sources()
    with pytest.raises(ValidationError, match="data class"):
        rebuild(reddit, data_class=C.DataClass.PUBLIC)
    with pytest.raises(ValidationError, match="data class"):
        rebuild(review, data_class=C.DataClass.REDDIT)


def test_a_source_is_never_private() -> None:
    with pytest.raises(ValidationError):
        rebuild(_web_source(), data_class=C.DataClass.PRIVATE)


def test_the_reddit_source_still_builds_without_the_new_fields() -> None:
    source = make_source(
        origin="arctic_shift",
        subreddit="developersIndia",
        thread_id="abc123",
        title="A thread",
        text="Some text from a thread.",
        published_at=None,
        author_digest=None,
        fetched_at=ex.T0,
    )
    assert source is not None
    assert source.data_class is C.DataClass.REDDIT
    assert source.comments == () and source.upvotes is None and source.comment_count is None


@pytest.mark.parametrize(
    "change",
    [{"upvotes": 5}, {"comment_count": 5}, {"comments": [_comment()]}],
    ids=["upvotes", "comment_count", "comments"],
)
def test_votes_and_comments_are_for_reddit_only(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="Reddit only"):
        rebuild(_web_source(), **change)


def test_upvotes_can_be_negative_but_a_comment_count_cannot() -> None:
    assert rebuild(ex.reddit_thread(), upvotes=-4).upvotes == -4
    with pytest.raises(ValidationError):
        rebuild(ex.reddit_thread(), comment_count=-1)


@pytest.mark.parametrize("depth", [1, 2, 3])
def test_a_comment_is_one_to_three_levels_deep(depth: int) -> None:
    assert C.SourceComment.model_validate(_comment(depth=depth)).depth == depth


@pytest.mark.parametrize("depth", [0, 4])
def test_a_comment_depth_of_0_or_4_is_invalid(depth: int) -> None:
    with pytest.raises(ValidationError):
        C.SourceComment.model_validate(_comment(depth=depth))


def test_a_comment_needs_text_and_a_capped_length() -> None:
    with pytest.raises(ValidationError):
        C.SourceComment.model_validate(_comment(text=""))
    with pytest.raises(ValidationError):
        C.SourceComment.model_validate(_comment(text="x" * (C.MAX_COMMENT_CHARS + 1)))


def test_comment_ids_are_unique_within_a_source() -> None:
    thread = ex.reddit_thread()
    with pytest.raises(ValidationError, match="once"):
        rebuild(thread, comments=[_comment(), _comment()])


def test_a_comment_author_hash_is_not_checked_by_the_model() -> None:
    """US-12 makes the hash. The model can't tell a hash from a name, so the field documents the rule instead."""
    assert C.SourceComment.model_validate(_comment(author_hash="throwaway_user_42")).author_hash == "throwaway_user_42"
    documented = exported("SourceComment")["properties"]["author_hash"]["description"]
    assert "salted" in documented.lower() and "never" in documented.lower()


def test_a_reply_below_the_top_level_names_its_parent() -> None:
    assert C.SourceComment.model_validate(_comment(depth=1, parent_id=None)).parent_id is None
    with pytest.raises(ValidationError, match="parent"):
        C.SourceComment.model_validate(_comment(depth=2, parent_id=None))
    with pytest.raises(ValidationError):
        C.SourceComment.model_validate(_comment(parent_id=""))


@pytest.mark.parametrize("change", [{"id": ""}, {"id": "x" * 33}, {"parent_id": "x" * 33}, {"author_hash": "x" * 129}])
def test_comment_fields_have_length_limits(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        C.SourceComment.model_validate(_comment(**change))


def test_a_comment_has_a_quality_score_for_us_13() -> None:
    assert C.SourceComment.model_validate(_comment()).quality == 0.5
    with pytest.raises(ValidationError):
        C.SourceComment.model_validate(_comment(quality=1.5))


def test_a_thread_keeps_at_most_the_comment_limit() -> None:
    too_many = [_comment(id=f"c{i}") for i in range(C.MAX_SOURCE_COMMENTS + 1)]
    with pytest.raises(ValidationError):
        rebuild(ex.reddit_thread(), comments=too_many)


def test_a_source_can_still_be_hashed() -> None:
    assert len({ex.reddit_thread(), ex.reddit_thread()}) == 1


def test_times_are_stored_in_utc() -> None:
    india = timezone(timedelta(hours=5, minutes=30))
    local = datetime(2026, 10, 3, 15, 30, tzinfo=india)
    assert rebuild(_web_source(), fetched_at=local).fetched_at.utcoffset() == timedelta(0)
    assert rebuild(_web_source(), fetched_at=local).fetched_at == datetime(2026, 10, 3, 10, 0, tzinfo=UTC)
    assert C.ProgressEvent.model_validate(_event(at=local)).at.utcoffset() == timedelta(0)
    assert _claim(extracted_at=local).extracted_at.utcoffset() == timedelta(0)
    assert _claim(retrieved_at=local).retrieved_at.utcoffset() == timedelta(0)


def test_a_date_too_far_away_to_convert_is_refused_cleanly() -> None:
    india = timezone(timedelta(hours=5, minutes=30))
    with pytest.raises(ValidationError, match="too far"):
        rebuild(_web_source(), fetched_at=datetime(1, 1, 1, tzinfo=india))


def test_a_page_date_keeps_any_valid_value() -> None:
    india = timezone(timedelta(hours=5, minutes=30))
    assert rebuild(_web_source(), published_at=datetime(1, 1, 1, tzinfo=india)).published_at is not None


def test_a_deleted_author_has_no_hash() -> None:
    assert C.SourceComment.model_validate(_comment(author_hash=None)).author_hash is None


# --- Claim (US-14, DR-05, build rule 4) ------------------------------------------------------------


def _claim(**changes: object) -> C.Claim:
    base = {**ex.claims()[0].model_dump(), "value": None, "score": 7.0}
    return C.Claim.model_validate({**base, **changes})


def test_a_claim_keeps_the_fields_stories_need() -> None:
    wanted = {
        "id", "source_id", "source_url", "product_mention", "is_product_type", "criterion_key", "value", "score",
        "quote", "claim_type", "retrieved_at", "extracted_at",
    }  # fmt: skip
    assert wanted <= set(C.Claim.model_fields)


def test_a_claim_with_both_value_and_score_is_invalid() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        _claim(value=1.4, score=7.0)


def test_a_claim_with_neither_value_nor_score_is_invalid() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        _claim(value=None, score=None)


@pytest.mark.parametrize("score", [-0.1, 10.1])
def test_a_score_outside_0_to_10_is_invalid(score: float) -> None:
    with pytest.raises(ValidationError):
        _claim(score=score)


@pytest.mark.parametrize("score", [0, 10])
def test_a_score_of_0_or_10_is_valid(score: float) -> None:
    assert _claim(score=score).score == score


@pytest.mark.parametrize("value", [1.4, 14, "IPS panel"])
def test_a_value_is_text_or_a_number(value: object) -> None:
    assert _claim(value=value, score=None).value == value


@pytest.mark.parametrize("value", [True, "", "x" * 201, float("nan"), float("inf")])
def test_a_value_cannot_be_a_boolean_empty_too_long_or_not_a_real_number(value: object) -> None:
    with pytest.raises(ValidationError):
        _claim(value=value, score=None)


def test_claim_type_is_experience_or_spec() -> None:
    assert {t.value for t in C.ClaimType} == {"experience", "spec"}
    with pytest.raises(ValidationError):
        _claim(claim_type="opinion")


def test_a_claim_must_carry_its_source_url_and_time() -> None:
    data = _claim().model_dump()
    for field in ("source_url", "retrieved_at", "extracted_at"):
        with pytest.raises(ValidationError):
            C.Claim.model_validate({k: v for k, v in data.items() if k != field})


@pytest.mark.parametrize("score", [True, "7"])
def test_a_score_cannot_be_a_boolean_or_text(score: object) -> None:
    with pytest.raises(ValidationError):
        _claim(score=score)


def test_a_scored_claim_must_name_its_criterion() -> None:
    with pytest.raises(ValidationError, match="criterion"):
        _claim(criterion_key=None)


def test_a_source_url_must_be_a_url() -> None:
    with pytest.raises(ValidationError):
        _claim(source_url="not a url")


@pytest.mark.parametrize("name", ["", "x" * 151])
def test_a_product_mention_has_length_limits(name: str) -> None:
    with pytest.raises(ValidationError):
        _claim(product_mention=name)
    with pytest.raises(ValidationError):
        _count(product_mention=name)


def test_a_mention_never_keeps_a_username() -> None:
    assert "author" not in C.ProductMention.model_fields
    assert "never" in exported("ProductMention")["properties"]["author_hash"]["description"]
    assert C.ProductMention(raw_name="Nova", is_product=True, author_hash="a" * 64).author_hash == "a" * 64
    for bad in ("throwaway_user_42", "A" * 64, "a" * 65):
        with pytest.raises(ValidationError):
            C.ProductMention(raw_name="Nova", is_product=True, author_hash=bad)


def test_extracted_at_must_be_timezone_aware() -> None:
    with pytest.raises(ValidationError):
        _claim(extracted_at=ex.T0.replace(tzinfo=None))


def test_a_claim_is_about_a_buyable_product_unless_it_says_otherwise() -> None:
    assert ex.claims()[0].is_product_type is False
    assert ex.product_type_claim().is_product_type is True


def test_the_product_type_example_quotes_its_source() -> None:
    claim, guide = ex.product_type_claim(), ex.product_guide()
    assert claim.source_id == guide.id and claim.source_url == guide.url
    assert claim.quote in guide.text


def test_example_claims_have_exactly_one_of_value_and_score() -> None:
    assert all((c.value is None) != (c.score is None) for c in ex.claims())


# --- Recommender counts (US-13) --------------------------------------------------------------------


def _count(**changes: object) -> C.RecommenderCount:
    return C.RecommenderCount.model_validate({**ex.recommender_counts()[0].model_dump(), **changes})


def test_a_recommender_count_names_people_and_threads() -> None:
    count = _count(distinct_recommenders=3, threads=2)
    assert (count.distinct_recommenders, count.threads) == (3, 2)


@pytest.mark.parametrize("change", [{"distinct_recommenders": -1}, {"threads": -1}], ids=["people", "threads"])
def test_a_recommender_count_cannot_be_negative(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _count(**change)


def test_people_and_threads_come_together() -> None:
    with pytest.raises(ValidationError, match="people and threads"):
        _count(distinct_recommenders=2, threads=0)
    with pytest.raises(ValidationError, match="people and threads"):
        _count(distinct_recommenders=0, threads=3)
    assert _count(distinct_recommenders=0, threads=0).threads == 0


def test_the_example_counts_agree_with_the_rest_of_the_example() -> None:
    by_name = {p.name: p.recommender_count for p in ex.match().products}
    assert all(by_name[c.product_mention] == c.distinct_recommenders for c in ex.recommender_counts())
    threads = sum(s.type is C.SourceType.REDDIT for s in ex.research().sources)
    assert all(c.threads <= threads for c in ex.recommender_counts())


def test_match_can_receive_the_counts() -> None:
    assert C.MatchInput(mentions=ex.mentions()).recommender_counts == []
    counts = ex.recommender_counts()
    assert C.MatchInput(mentions=ex.mentions(), recommender_counts=counts).recommender_counts == counts


def test_research_has_no_counts_until_us_13_adds_them() -> None:
    assert C.ResearchResult(queries=["laptops"]).recommender_counts == []
    assert ex.research().recommender_counts == ex.recommender_counts()


def test_each_product_is_counted_once() -> None:
    with pytest.raises(ValidationError, match="once"):
        rebuild(ex.research(), recommender_counts=[_count().model_dump(), _count().model_dump()])
    with pytest.raises(ValidationError, match="once"):
        rebuild(
            ex.research(), recommender_counts=[_count(product_mention="Nova 14"), _count(product_mention="nova 14")]
        )


def test_match_keeps_at_most_200_counts() -> None:
    too_many = [_count(product_mention=f"Product {i}") for i in range(201)]
    with pytest.raises(ValidationError):
        C.MatchInput(mentions=ex.mentions(), recommender_counts=too_many)


def test_research_keeps_at_most_200_counts() -> None:
    too_many = [_count(product_mention=f"Product {i}").model_dump() for i in range(201)]
    with pytest.raises(ValidationError):
        rebuild(ex.research(), recommender_counts=too_many)


# --- Rough answers (US-08) -------------------------------------------------------------------------


def test_the_table_is_the_one_us_08_sets() -> None:
    table = {level.value: weight for level, weight in C.ROUGH_LEVEL_WEIGHTS.items()}
    assert table == {"low": 2, "medium": 5, "high": 8, "good_enough": 3}


def test_the_table_covers_every_level() -> None:
    assert set(C.ROUGH_LEVEL_WEIGHTS) == set(C.RoughLevel)
    with pytest.raises(ValidationError, match="every level"):
        C.RoughLevelWeights(weights={C.RoughLevel.LOW: 2})


def test_the_exported_table_matches_the_constant() -> None:
    data = json.loads((SHARED / "examples" / "rough_levels.json").read_text(encoding="utf-8"))
    assert data == {"weights": {level.value: weight for level, weight in C.ROUGH_LEVEL_WEIGHTS.items()}}


def test_the_table_agrees_with_the_interview_until_us_08_joins_them() -> None:
    for level in (C.RoughLevel.LOW, C.RoughLevel.MEDIUM, C.RoughLevel.HIGH):
        assert REPLY_WEIGHTS[level.value] == C.ROUGH_LEVEL_WEIGHTS[level]


def test_a_reply_can_carry_a_level() -> None:
    assert ex.reply_rough().level is C.RoughLevel.GOOD_ENOUGH


def test_a_reply_can_carry_an_exact_weight() -> None:
    assert ex.reply_exact().exact_weight == 7.5


def test_a_plain_reply_still_works() -> None:
    reply = C.ReplyRequest(reply_text="Medium")
    assert reply.level is None and reply.exact_weight is None


def test_a_reply_with_both_level_and_exact_weight_is_invalid() -> None:
    with pytest.raises(ValidationError, match="never both"):
        C.ReplyRequest(reply_text="High", level=C.RoughLevel.HIGH, exact_weight=8)


def test_an_interview_turn_follows_the_same_rule() -> None:
    state = C.InterviewState(session_id=ex.SESSION_ID, intent=ex.intent(), criteria=ex.criteria())
    assert C.InterviewTurn(state=state, level=C.RoughLevel.LOW).level is C.RoughLevel.LOW
    assert C.InterviewTurn(state=state, exact_weight=4).exact_weight == 4
    with pytest.raises(ValidationError, match="never both"):
        C.InterviewTurn(state=state, level=C.RoughLevel.LOW, exact_weight=4)


@pytest.mark.parametrize("weight", [-1, 10.5])
def test_an_exact_weight_outside_0_to_10_is_invalid(weight: float) -> None:
    with pytest.raises(ValidationError):
        C.ReplyRequest(reply_text="x", exact_weight=weight)


@pytest.mark.parametrize("weight", [True, "7"])
def test_an_exact_weight_cannot_be_a_boolean_or_text(weight: object) -> None:
    with pytest.raises(ValidationError):
        C.ReplyRequest.model_validate({"reply_text": "x", "exact_weight": weight})


@pytest.mark.parametrize("weight", [True, "7", 10.5])
def test_an_interview_turn_refuses_a_bad_exact_weight(weight: object) -> None:
    state = C.InterviewState(session_id=ex.SESSION_ID, intent=ex.intent(), criteria=ex.criteria())
    with pytest.raises(ValidationError):
        C.InterviewTurn.model_validate({"state": state.model_dump(), "exact_weight": weight})


def test_an_unknown_level_is_invalid() -> None:
    with pytest.raises(ValidationError):
        C.ReplyRequest.model_validate({"reply_text": "x", "level": "extreme"})


def test_a_client_may_leave_both_out_of_the_reply() -> None:
    required = exported("ReplyRequest")["required"]
    assert "level" not in required and "exact_weight" not in required


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(env={})) as test_client:
        yield test_client


REPLY_URL = "/api/presearch/sess_000000000000000000000000/interview/reply"


def test_the_route_refuses_both_a_level_and_an_exact_weight(client: TestClient) -> None:
    answer = client.post(REPLY_URL, json={"reply_text": "High", "level": "high", "exact_weight": 8})
    assert answer.status_code == 422
    assert "never both" in answer.json()["detail"]


@pytest.mark.parametrize("extra", [{"level": "high"}, {"exact_weight": 8}], ids=["level", "exact_weight"])
def test_the_route_accepts_either_one(client: TestClient, extra: dict[str, object]) -> None:
    # Past validation: the only complaint left is that this session doesn't exist.
    assert client.post(REPLY_URL, json={"reply_text": "High", **extra}).status_code == 404


# --- Search runs (P-07) ----------------------------------------------------------------------------


RUN_ID = ex.RUN_ID


def _event(event_id: int = 1, **changes: object) -> dict[str, object]:
    return {**ex.progress_events()[0].model_dump(), "event_id": event_id, **changes}


def _finished_at() -> object:
    return ex.T0 + timedelta(seconds=94)


def test_a_run_id_is_srch_and_24_hex_characters() -> None:
    assert C.SearchRunStarted(search_id=RUN_ID).search_id == RUN_ID


@pytest.mark.parametrize(
    "bad", ["srch_demo0001", "srch_" + "a" * 23, "srch_" + "a" * 25, "srch_" + "A" * 24, "run_" + "a" * 24]
)
def test_any_other_run_id_is_invalid(bad: str) -> None:
    with pytest.raises(ValidationError):
        C.SearchRunStarted(search_id=bad)


def test_run_statuses_are_the_agreed_six() -> None:
    assert {s.value for s in C.SearchRunStatus} == {"queued", "running", "done", "partial", "failed", "cancelled"}


def test_a_queued_run_has_not_started() -> None:
    run = C.SearchRun(search_id=RUN_ID, status=C.SearchRunStatus.QUEUED)
    assert run.started_at is None and run.progress == [] and run.result is None
    with pytest.raises(ValidationError, match="queued"):
        C.SearchRun(search_id=RUN_ID, status=C.SearchRunStatus.QUEUED, started_at=ex.T0)


def test_a_running_run_has_a_start_time() -> None:
    with pytest.raises(ValidationError, match="started_at"):
        C.SearchRun(search_id=RUN_ID, status=C.SearchRunStatus.RUNNING)


@pytest.mark.parametrize("status", ["done", "partial"])
def test_a_run_with_a_result_has_a_start_time(status: str) -> None:
    with pytest.raises(ValidationError, match="started_at"):
        rebuild(ex.search_run_done(), status=status, started_at=None)


def test_a_run_that_failed_before_starting_is_valid() -> None:
    run = C.SearchRun(search_id=RUN_ID, status=C.SearchRunStatus.FAILED, finished_at=ex.T0)
    assert run.started_at is None and run.result is None


def test_a_run_keeps_at_most_the_event_limit() -> None:
    too_many = [_event(i) for i in range(1, MAX_RUN_EVENTS + 2)]
    with pytest.raises(ValidationError):
        rebuild(ex.search_run_running(), progress=too_many)


def test_a_running_run_has_no_end_and_no_result() -> None:
    running = ex.search_run_running()
    with pytest.raises(ValidationError, match="finished_at"):
        rebuild(running, finished_at=_finished_at())
    with pytest.raises(ValidationError, match="result"):
        rebuild(running, result=ex.search_run_done().result)


@pytest.mark.parametrize("status", ["done", "partial", "failed", "cancelled"])
def test_a_finished_run_has_an_end_time(status: str) -> None:
    with pytest.raises(ValidationError, match="finished_at"):
        C.SearchRun.model_validate(
            {"search_id": RUN_ID, "status": status, "started_at": ex.T0, "result": ex.search_run_done().result}
        )


def test_a_run_cannot_end_before_it_starts() -> None:
    with pytest.raises(ValidationError, match="before"):
        rebuild(ex.search_run_done(), finished_at=ex.T0 - timedelta(seconds=1))


def test_a_done_run_carries_its_result() -> None:
    done = ex.search_run_done()
    assert done.result is not None and done.result.search_id == RUN_ID
    with pytest.raises(ValidationError, match="result"):
        rebuild(done, result=None)


def test_a_partial_run_carries_a_partial_result() -> None:
    done = ex.search_run_done()
    assert done.result is not None
    partial = rebuild(done.result, partial=True, coverage_note="The 150-second limit was reached.")
    assert rebuild(done, status="partial", result=partial).status is C.SearchRunStatus.PARTIAL
    with pytest.raises(ValidationError, match="partial"):
        rebuild(done, status="partial")
    with pytest.raises(ValidationError, match="partial"):
        rebuild(done, result=partial)


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_a_failed_or_cancelled_run_has_no_result(status: str) -> None:
    done = ex.search_run_done()
    assert rebuild(done, status=status, result=None).result is None
    with pytest.raises(ValidationError, match="result"):
        rebuild(done, status=status)


def test_a_run_cancelled_while_queued_never_started() -> None:
    run = C.SearchRun(search_id=RUN_ID, status=C.SearchRunStatus.CANCELLED, finished_at=ex.T0)
    assert run.started_at is None


def test_a_result_must_belong_to_its_run() -> None:
    done = ex.search_run_done()
    assert done.result is not None
    other = rebuild(done.result, search_id="srch_" + "b" * 24)
    with pytest.raises(ValidationError, match="this run"):
        rebuild(done, result=other)


def test_progress_must_belong_to_its_run() -> None:
    running = ex.search_run_running()
    with pytest.raises(ValidationError, match="this run"):
        rebuild(running, progress=[_event(search_id="srch_" + "b" * 24)])


@pytest.mark.parametrize("ids", [[2], [1, 3], [1, 1], [2, 1]])
def test_progress_counts_up_from_1(ids: list[int]) -> None:
    with pytest.raises(ValidationError, match="count up"):
        rebuild(ex.search_run_running(), progress=[_event(i) for i in ids])


def test_a_progress_event_id_starts_at_1() -> None:
    with pytest.raises(ValidationError):
        C.ProgressEvent.model_validate(_event(0))


def test_progress_statuses_are_the_agreed_four() -> None:
    for status in ("started", "done", "fallback", "failed"):
        assert C.ProgressEvent.model_validate(_event(status=status)).status == status
    with pytest.raises(ValidationError):
        C.ProgressEvent.model_validate(_event(status="finished"))


def test_a_progress_event_needs_a_short_message() -> None:
    for bad in ("", "x" * 201):
        with pytest.raises(ValidationError):
            C.ProgressEvent.model_validate(_event(message=bad))


def test_a_progress_event_counts_sources_by_type() -> None:
    event = C.ProgressEvent.model_validate(_event(sources_found={"reddit": 12, "review_site": 3}))
    assert event.sources_found == {C.SourceType.REDDIT: 12, C.SourceType.REVIEW_SITE: 3}
    assert C.ProgressEvent.model_validate(_event(sources_found={})).sources_found == {}


@pytest.mark.parametrize("bad", [{"reddit": -1}, {"podcast": 1}], ids=["negative", "unknown type"])
def test_bad_source_counts_are_invalid(bad: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        C.ProgressEvent.model_validate(_event(sources_found=bad))


def test_post_search_keeps_its_contract() -> None:
    assert set(C.SearchRequest.model_fields) == {"session_id", "user_id", "raw_query", "rubric"}
    assert set(C.SearchResult.model_fields) == {
        "schema_version", "search_id", "request", "started_at", "finished_at", "partial", "products", "decision",
        "explanations", "commerce", "sources", "stage_reports", "coverage_note", "saved",
    }  # fmt: skip


# --- Export ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "schema"),
    [
        ("reddit_thread", "Source"),
        ("product_type_claim", "Claim"),
        ("reply_rough", "ReplyRequest"),
        ("reply_exact", "ReplyRequest"),
        ("rough_levels", "RoughLevelWeights"),
        ("search_run_started", "SearchRunStarted"),
        ("search_run_running", "SearchRun"),
        ("search_run_done", "SearchRun"),
    ],
)
def test_each_new_example_is_exported_and_matches_its_schema(name: str, schema: str) -> None:
    data = json.loads((SHARED / "examples" / f"{name}.json").read_text(encoding="utf-8"))
    assert schema_errors(data, exported(schema)) == []


@pytest.mark.parametrize(
    ("name", "schema"), [("recommender_counts", "RecommenderCount"), ("progress_events", "ProgressEvent")]
)
def test_each_new_list_example_matches_its_schema(name: str, schema: str) -> None:
    items = json.loads((SHARED / "examples" / f"{name}.json").read_text(encoding="utf-8"))
    assert items and all(schema_errors(item, exported(schema)) == [] for item in items)


def test_the_exported_schema_refuses_an_unknown_source_type_in_the_counts() -> None:
    good = ex.progress_events()[1].model_dump(mode="json")
    assert schema_errors({**good, "sources_found": {"podcast": 1}}, exported("ProgressEvent")) != []


def test_the_schema_version_was_bumped_for_these_changes() -> None:
    assert (SHARED / "VERSION").read_text(encoding="utf-8").strip() == C.SCHEMA_VERSION == "1.6.0"
