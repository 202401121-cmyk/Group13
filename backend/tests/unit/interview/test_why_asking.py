"""Why a question comes now (US-04, S4-5): the smart interview's why_asking, in plain words, from code (no LLM).

It says the criterion matters and is still unsure, then gives the definition when the whole fits in 200 characters.
Questions in the fixed order (the fixed stage, a turn that fell back) keep the definition alone.
"""

from __future__ import annotations

import pytest

from app.contracts import CriteriaSet, Criterion, InterviewState, InterviewTurn, StageContext, WeightBelief
from app.contracts import examples as ex
from app.interview import AdaptiveInterviewStage, InterviewStage
from app.interview.beliefs import BeliefSettings, beliefs_for
from app.interview.why import MAX_WHY, _within, why_now
from tests.fakes.llm import FakeLLMClient

CTX = StageContext(search_id="srch_whyasking01")
SETTINGS = BeliefSettings()


def criterion(label: str = "Battery life", definition: str = "How long it lasts on one charge.") -> Criterion:
    return Criterion(key="battery_life", label=label, definition=definition, default_weight=7)


def prior(mean: float) -> WeightBelief:
    return WeightBelief(mean=mean, sd=SETTINGS.prior_sd)


@pytest.mark.parametrize(
    ("mean", "words"),
    [
        (9.0, "usually matters a lot"),
        (7.0, "usually matters a lot"),
        (6.6, "usually matters a lot"),  # 6.6 is about 7 out of 10
        (6.5, "usually matters a lot"),  # a half rounds up
        (6.4, "usually matters"),
        (4.0, "usually matters"),
        (3.5, "usually matters"),
        (3.4, "can matter"),
        (0.0, "can matter"),
    ],
)
def test_how_much_it_matters_comes_from_the_beliefs_mean(mean: float, words: str) -> None:
    text = why_now(criterion(), prior(mean), SETTINGS)
    assert text.startswith(f"Battery life: it {words} and ")


def test_a_criterion_never_asked_is_not_known_yet_and_one_asked_before_is_not_sure_yet() -> None:
    assert "we don't know your view yet, so we ask now." in why_now(criterion(), prior(7.0), SETTINGS)
    surer = WeightBelief(mean=7.0, sd=1.0)
    assert "we're not sure of your view yet, so we ask now." in why_now(criterion(), surer, SETTINGS)


def test_the_definition_follows_when_it_fits() -> None:
    text = why_now(criterion(), prior(7.0), SETTINGS)
    assert text == (
        "Battery life: it usually matters a lot and we don't know your view yet, so we ask now. "
        "How long it lasts on one charge."
    )


def test_a_longer_definition_still_fits_beside_the_shorter_reason() -> None:
    # 114 characters: 201 beside the full reason, 187 beside the shorter one
    definition = (
        "How many hours it runs on one charge during real work, so you can use it all day away from a plug or a power "
        "bank."
    )
    text = why_now(criterion(definition=definition), prior(7.0), SETTINGS)
    assert text == f"Battery life: it usually matters a lot, and we don't know your view yet. {definition}"


def test_a_definition_that_does_not_fit_either_way_is_left_out_whole() -> None:
    long_definition = "How long it lasts on one charge, " + "with the screen on and the radios busy, " * 4 + "in a day."
    text = why_now(criterion(definition=long_definition), prior(7.0), SETTINGS)
    assert text == "Battery life: it usually matters a lot and we don't know your view yet, so we ask now."


def test_a_plural_label_reads_right() -> None:
    plural = Criterion(key="memory_and_storage", label="RAM and storage", definition="How much it holds.")
    assert why_now(plural, prior(7.0), SETTINGS).startswith("RAM and storage: it usually matters a lot and ")


def test_it_always_fits_200_characters_and_never_cuts_a_word() -> None:
    label = "Very long criterion label that runs to exactly sixty chars xx"[:60]
    text = why_now(criterion(label=label, definition="x " * 140), prior(9.0), SETTINGS)
    assert len(text) <= MAX_WHY == 200
    assert text.endswith("so we ask now.")
    for mean in (0.0, 5.0, 10.0):
        for sd in (0.5, SETTINGS.prior_sd):
            assert len(why_now(criterion(label=label), WeightBelief(mean=mean, sd=sd), SETTINGS)) <= MAX_WHY


def test_the_length_guard_cuts_at_a_whole_word_and_never_passes_the_limit() -> None:
    assert _within("short enough", 20) == "short enough"
    assert _within("one two three four", 13) == "one two three"
    assert _within("x" * 30, 20) == "x" * 20  # no space to cut at: a hard cut, still within the limit
    assert len(_within("y" * 201 + " z", 200)) == 200


def test_the_label_is_kept_exactly_as_written() -> None:
    text = why_now(criterion(label="USB-C/Thunderbolt ports"), prior(5.0), SETTINGS)
    assert text.startswith("USB-C/Thunderbolt ports: it usually matters and ")


# --- through the stage ----------------------------------------------------------------------------------------------


def _state(criteria: CriteriaSet | None = None) -> InterviewState:
    return InterviewState(session_id=ex.SESSION_ID, intent=ex.intent(), criteria=criteria or ex.criteria())


class NoLLM(FakeLLMClient):
    def complete(self, *args: object, **kwargs: object) -> object:  # type: ignore[override]
        raise AssertionError("why_asking must never ask an LLM")


def test_each_smart_question_says_why_it_comes_now_without_an_llm() -> None:
    stage = AdaptiveInterviewStage(llm=NoLLM())
    step = stage.run(InterviewTurn(state=_state()), CTX)
    for _ in range(4):
        assert step.question is not None
        key = step.question.criterion_key
        crit = ex.criteria().get(key)
        assert crit is not None
        before = beliefs_for(_drop_pending(step.state), stage.settings)[key]
        assert step.question.why_asking == why_now(crit, before, stage.settings)
        step = stage.run(InterviewTurn(state=step.state, reply_text="High"), CTX)


def _drop_pending(state: InterviewState) -> InterviewState:
    """The state the planner saw when it chose the pending question."""
    history = [e.model_dump() for e in state.history if e.status != "pending"]
    return InterviewState.model_validate({**state.model_dump(), "history": history})


def _with_stored_words(state: InterviewState, words: str) -> InterviewState:
    """The state with its first question's why_asking set to words the code would never write."""
    history = [e.model_dump() for e in state.history]
    history[0]["question"]["why_asking"] = words
    return InterviewState.model_validate({**state.model_dump(), "history": history})


def test_a_reload_or_going_back_keeps_the_words_the_question_was_first_asked_with() -> None:
    stage = AdaptiveInterviewStage()
    first = stage.run(InterviewTurn(state=_state()), CTX)
    stored = _with_stored_words(first.state, "The words it was asked with.")
    again = stage.run(InterviewTurn(state=stored), CTX)  # a reload
    assert again.question is not None and again.question.why_asking == "The words it was asked with."
    second = stage.run(InterviewTurn(state=stored, reply_text="High"), CTX)
    back = stage.go_back(second.state, "q_cpu_performance")
    assert back.question is not None and back.question.why_asking == "The words it was asked with."


def test_the_fixed_order_keeps_the_definition() -> None:
    step = InterviewStage().run(InterviewTurn(state=_state()), CTX)
    assert step.question is not None
    cpu = ex.criteria().get("cpu_performance")
    assert cpu is not None and step.question.why_asking == cpu.definition[:MAX_WHY].rstrip()


def test_a_turn_that_fell_back_to_the_fixed_order_keeps_the_definition(monkeypatch: pytest.MonkeyPatch) -> None:
    stage = AdaptiveInterviewStage()
    first = stage.run(InterviewTurn(state=_state()), CTX)

    def explode(*_: object, **__: object) -> object:
        raise RuntimeError("no")

    monkeypatch.setattr("app.interview.adaptive.next_key", explode)
    step = stage.run(InterviewTurn(state=first.state, reply_text="High"), CTX)
    assert step.ordering == "fixed_order" and step.question is not None
    battery = ex.criteria().get("battery_life")
    assert battery is not None and step.question.why_asking == battery.definition[:MAX_WHY].rstrip()
