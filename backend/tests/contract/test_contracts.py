"""Contract tests (plan §14, layer 2). Every rule in app/contracts must have a test here.

Run: pytest tests/contract -q
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TypeVar, cast

import pytest
from pydantic import BaseModel, ValidationError

import app.contracts as C
from app.contracts import examples as ex

M = TypeVar("M", bound=BaseModel)


def rebuild(model: M, **changes: object) -> M:
    """Copy a model with changes AND re-run validation (model_copy would skip it)."""
    return type(model).model_validate({**model.model_dump(), **changes})


# --- examples and round trips -----------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(ex.ALL_EXAMPLES))
def test_every_example_is_valid_and_round_trips(name: str) -> None:
    obj = ex.ALL_EXAMPLES[name]()
    items = cast(list[BaseModel], obj if isinstance(obj, list) else [obj])
    for item in items:
        again = type(item).model_validate_json(item.model_dump_json())
        assert again == item


def test_every_contract_exports_a_json_schema() -> None:
    contracts = [
        v for v in vars(C).values() if isinstance(v, type) and issubclass(v, C.Contract) and v is not C.Contract
    ]
    assert len(contracts) > 30
    for model in contracts:
        assert model.model_json_schema()["type"] == "object"


# --- base rules ---------------------------------------------------------------------------------


def test_contracts_are_frozen() -> None:
    with pytest.raises(ValidationError):
        ex.intent().category = "phone"  # type: ignore[misc]


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        C.Money(amount=10, currency="INR", discount=5)  # type: ignore[call-arg]


def test_naive_datetimes_are_rejected() -> None:
    src = ex.sources()[0]
    with pytest.raises(ValidationError, match="timezone-aware"):
        rebuild(src, fetched_at=datetime(2026, 10, 3, 10, 0))


def test_every_stage_has_a_time_limit() -> None:
    assert set(C.STAGE_TIME_LIMITS_S) == set(C.StageName)
    assert C.SEARCH_TIME_LIMIT_S == 150


# --- intake -------------------------------------------------------------------------------------


def test_no_category_requires_clarification() -> None:
    with pytest.raises(ValidationError, match="needs_clarification"):
        C.Intent(raw_query="something nice", detected_by="llm")


def test_unclear_request_is_not_ready_for_research() -> None:
    assert ex.unclear_intent().ready_for_research is False
    assert ex.intent().ready_for_research is True


def test_budget_min_above_max_is_rejected() -> None:
    with pytest.raises(ValidationError, match="above budget max"):
        C.Budget(min=C.Money(amount=90000), max=C.Money(amount=70000))


# --- criteria and rubric ------------------------------------------------------------------------


def test_criteria_need_exactly_one_value_for_money() -> None:
    cs = ex.criteria()
    no_vfm = [c.model_dump() | {"is_value_for_money": False} for c in cs.criteria]
    with pytest.raises(ValidationError, match="value for money"):
        C.CriteriaSet(category="laptop", criteria=no_vfm, source="generated")


def test_vague_criterion_is_rejected() -> None:
    with pytest.raises(ValidationError, match="too vague"):
        C.Criterion(key="quality", label="Quality", definition="How good the product is overall.")


def test_too_few_criteria_are_rejected() -> None:
    cs = ex.criteria()
    with pytest.raises(ValidationError):
        C.CriteriaSet(category="laptop", criteria=cs.criteria[:5], source="generated")


def test_rubric_weights_must_add_up_to_one() -> None:
    rb = ex.rubric()
    bad = [w.model_dump() for w in rb.weights]
    bad[0]["normalized"] += 0.1
    with pytest.raises(ValidationError, match="add up to 1"):
        rebuild(rb, weights=bad)


def test_normalize_handles_all_zero_weights() -> None:
    assert C.normalize({"a": 0, "b": 0}) == {"a": 0.5, "b": 0.5}


def test_rubric_fingerprint_changes_with_priorities() -> None:
    rb = ex.rubric()
    assert rb.fingerprint() == ex.rubric().fingerprint()
    shares = C.normalize({"battery_life": 10, "cpu_performance": 1})
    other = rebuild(
        rb, weights=[{"key": k, "weight": 5, "normalized": v, "origin": "manual"} for k, v in shares.items()]
    )
    assert other.fingerprint() != rb.fingerprint()


# --- interview ----------------------------------------------------------------------------------


def _state() -> C.InterviewState:
    return C.InterviewState(session_id=ex.SESSION_ID, intent=ex.intent(), criteria=ex.criteria())


def _question() -> C.InterviewQuestion:
    return C.InterviewQuestion(
        id="q_battery_life",
        criterion_key="battery_life",
        text="How long do you need to work away from a plug?",
        quick_replies=["Under 4 hours", "A full workday", "It doesn't matter"],
    )


def test_interview_cannot_start_without_a_category() -> None:
    with pytest.raises(ValidationError, match="category is known"):
        C.InterviewState(session_id=ex.SESSION_ID, intent=ex.unclear_intent(), criteria=ex.criteria())


def test_finished_step_needs_a_rubric_and_no_question() -> None:
    with pytest.raises(ValidationError, match="finished"):
        C.InterviewStep(kind="finished", message="Done", question=_question(), state=_state(), questions_asked=4)
    ok = C.InterviewStep(kind="finished", message="Done", rubric=ex.rubric(), state=_state(), questions_asked=4)
    assert ok.rubric is not None


def test_answering_a_user_question_records_where_the_answer_came_from() -> None:
    with pytest.raises(ValidationError, match="answer_source"):
        C.InterviewStep(
            kind="answer_then_repeat",
            message="ANC cuts outside noise.",
            question=_question(),
            state=_state(),
            questions_asked=2,
        )
    step = C.InterviewStep(
        kind="answer_then_repeat",
        message="ANC cuts outside noise.",
        question=_question(),
        answer_source="definition",
        state=_state(),
        questions_asked=2,
    )
    assert step.answer_source == "definition"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"reply_type": "answer"}, "must carry a weight"),
        ({"reply_type": "question"}, "user's question"),
        ({"reply_type": "correction", "weight": 5}, "which question"),
    ],
)
def test_reply_classification_rules(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        C.ReplyClassification(**kwargs)


def test_off_topic_and_unclear_replies_need_nothing_else() -> None:
    assert C.ReplyClassification(reply_type="off_topic").weight is None
    assert C.ReplyClassification(reply_type="unclear").weight is None


def test_unclear_count_is_capped_at_two() -> None:
    with pytest.raises(ValidationError):
        C.AskedQuestion(question=_question(), status="pending", unclear_count=3)


# --- research and claims ------------------------------------------------------------------------


def test_reddit_sources_are_community_evidence_with_a_subreddit() -> None:
    reddit = ex.sources()[0]
    with pytest.raises(ValidationError, match="subreddit"):
        rebuild(reddit, community=None)
    with pytest.raises(ValidationError, match="community evidence"):
        rebuild(reddit, tier=C.SourceTier.EDITORIAL)


def test_claim_needs_a_value_or_a_score_and_a_quote() -> None:
    c = ex.claims()[0]
    with pytest.raises(ValidationError, match="value, a score"):
        rebuild(c, value=None, score=None)
    with pytest.raises(ValidationError):
        rebuild(c, quote="")


def test_extraction_claims_must_belong_to_their_source() -> None:
    claim = ex.claims()[0]
    other = next(s.id for s in ex.sources() if s.id != claim.source_id)
    with pytest.raises(ValidationError, match="this result's source"):
        C.ExtractionResult(source_id=other, claims=[claim])


def test_alias_map_cannot_point_to_unknown_products() -> None:
    m = ex.match()
    with pytest.raises(ValidationError, match="unknown products"):
        rebuild(m, alias_map={"Ghost Laptop": "prd_ghost"})


# --- verify -------------------------------------------------------------------------------------


def _value(**kw: object) -> C.ResolvedValue:
    base: dict[str, object] = {"product_id": "prd_nova_book_14", "criterion_key": "battery_life", "confidence": 0.5}
    return C.ResolvedValue(**(base | kw))


def test_unknown_values_never_get_a_score() -> None:
    with pytest.raises(ValidationError, match="never become zero"):
        _value(state="unknown", score=0.0)


def test_disputed_values_cite_both_sides() -> None:
    with pytest.raises(ValidationError, match="conflicting"):
        _value(state="disputed", score=6, supporting_claim_ids=["clm_aaaaaaaa"])


def test_estimated_values_have_no_claims_of_their_own() -> None:
    with pytest.raises(ValidationError, match="peer-average"):
        _value(state="estimated", score=6, supporting_claim_ids=["clm_aaaaaaaa"])


def test_one_value_per_product_and_criterion() -> None:
    vr = ex.verify()
    with pytest.raises(ValidationError, match="one resolved value"):
        rebuild(vr, values=[v.model_dump() for v in vr.values] + [vr.values[0].model_dump()])


def test_dropped_products_cannot_keep_values() -> None:
    vr = ex.verify()
    with pytest.raises(ValidationError, match="dropped product"):
        rebuild(vr, dropped_unsourced=[vr.values[0].product_id])


# --- decide -------------------------------------------------------------------------------------


def test_a_product_cannot_be_ranked_and_filtered() -> None:
    d = ex.decision()
    clash = [{"product_id": d.ranked[0].product_id, "group": "filtered_out", "reasons": ["over budget"]}]
    with pytest.raises(ValidationError, match="both ranked and filtered"):
        rebuild(d, filtered=clash)


def test_nothing_passing_needs_a_relaxation_hint() -> None:
    d = ex.decision()
    with pytest.raises(ValidationError, match="relaxation hint"):
        rebuild(d, ranked=[])
    assert rebuild(d, ranked=[], relaxation_hint="Raising the budget to ₹85,000 adds 1 product.")


def test_simulated_ranking_follows_chance_of_first() -> None:
    d = ex.decision()
    swapped = [r.model_dump() for r in d.ranked]
    swapped[0]["chance_first"], swapped[1]["chance_first"] = 0.1, 0.8
    with pytest.raises(ValidationError, match="chance of being first"):
        rebuild(d, ranked=swapped)


def test_ranks_must_be_consecutive() -> None:
    d = ex.decision()
    bad = [r.model_dump() for r in d.ranked]
    bad[1]["rank"] = 5
    with pytest.raises(ValidationError, match="1, 2, 3"):
        rebuild(d, ranked=bad)


# --- explain, commerce, pipeline ----------------------------------------------------------------


def test_checked_explanations_cannot_keep_unsupported_sentences() -> None:
    e = ex.explanations()[0]
    bad = [s.model_dump() for s in e.sentences]
    bad[0]["supported"] = False
    with pytest.raises(ValidationError, match="unsupported"):
        rebuild(e, sentences=bad)


def test_generated_sentences_must_cite_claims_but_templates_need_not() -> None:
    e = ex.explanations()[0]
    uncited = [{"text": "A great laptop for you.", "claim_ids": []}]
    with pytest.raises(ValidationError, match="cite at least one claim"):
        rebuild(e, sentences=uncited, method="llm_unchecked")
    assert rebuild(e, sentences=uncited, method="template")


def test_offers_must_match_the_model_name() -> None:
    o = ex.commerce().offers[0]
    with pytest.raises(ValidationError, match="title matches"):
        rebuild(o, title_matched=False)


def test_prices_go_stale_after_24_hours() -> None:
    fresh, old = ex.commerce().offers
    now = ex.T0
    assert not fresh.is_stale(now)
    assert old.is_stale(now)


def test_degraded_search_must_explain_itself() -> None:
    r = ex.search_result()
    with pytest.raises(ValidationError, match="coverage_note"):
        rebuild(r, coverage_note=None)


def test_ranked_products_must_be_in_the_product_list() -> None:
    r = ex.search_result()
    with pytest.raises(ValidationError, match="missing from products"):
        rebuild(r, products=[])


def test_search_cannot_finish_before_it_starts() -> None:
    r = ex.search_result()
    with pytest.raises(ValidationError, match="before started_at"):
        rebuild(r, finished_at=r.started_at - timedelta(seconds=1))


# --- LLM wrapper interface ----------------------------------------------------------------------


def test_input_budget_leaves_room_for_answer_and_margin() -> None:
    m = C.ModelLimits(
        provider="groq",
        model="example-8k",
        context_window_tokens=8000,
        max_output_tokens=1024,
        trains_on_free_tier=False,
    )
    assert m.input_budget(reserve_output=1024) == 8000 - 800 - 1024
    with pytest.raises(ValueError, match="too small"):
        m.input_budget(reserve_output=8000)


def test_personal_tasks_include_everything_with_user_words() -> None:
    assert C.LLMTask.CLASSIFY_REPLY in C.PERSONAL_TASKS
    assert C.LLMTask.DETECT_INTENT in C.PERSONAL_TASKS
    assert C.LLMTask.EXTRACT_CLAIMS not in C.PERSONAL_TASKS  # public web text only


def test_generate_criteria_is_a_personal_task() -> None:
    # The criteria request carries the user's own words as the use case, so it must stay off
    # Gemini's free tier (build rule 8; team decision for US-03).
    assert C.LLMTask.GENERATE_CRITERIA in C.PERSONAL_TASKS


def test_call_record_is_valid() -> None:
    rec = C.LLMCallRecord(
        task="extract_claims",
        provider="gemini",
        model="flash",
        input_tokens=2900,
        output_tokens=400,
        duration_ms=1800,
        outcome="ok",
        fitted="trimmed",
        at=datetime.now(UTC),
    )
    assert rec.fitted == "trimmed"


# --- US-12: where a Reddit source came from, and who wrote it (hashed) -----------------------------


def _reddit_source(**changes: object) -> C.Source:
    reddit = next(s for s in ex.sources() if s.type == C.SourceType.REDDIT)
    return C.Source.model_validate({**reddit.model_dump(), **changes})


def test_a_reddit_source_records_which_archive_it_came_from() -> None:
    for origin in ("arctic_shift", "pullpush", "official"):
        assert _reddit_source(source_detail=origin).source_detail == origin
    assert _reddit_source().source_detail is None  # older sources, recorded before US-12
    with pytest.raises(ValidationError):
        _reddit_source(source_detail="pushshift")


def test_source_detail_is_for_reddit_only() -> None:
    web = next(s for s in ex.sources() if s.type != C.SourceType.REDDIT)
    with pytest.raises(ValidationError, match="Reddit only"):
        C.Source.model_validate({**web.model_dump(), "source_detail": "arctic_shift"})


def test_author_hash_is_a_sha256_never_a_username() -> None:
    digest = "a" * 64
    assert _reddit_source(author_hash=digest).author_hash == digest
    for bad in ("throwaway_user_42", "A" * 64, "a" * 63):
        with pytest.raises(ValidationError):
            _reddit_source(author_hash=bad)
