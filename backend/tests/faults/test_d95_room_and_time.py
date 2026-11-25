"""D-95 (from S5-6a): extraction's time and room, and the order of enrich's price look-ups. Each test here fails
without its change. Fake providers and made-up sources only; no real API is called.

(a) A public page's first extraction attempt gets the call's whole time (less a margin that keeps it inside the
    source's 15 s) only when no other model on its route has room at that moment; otherwise the 0.6 share stays.
(b) A Reddit thread or a video's comments waits for Groq's Reddit lane inside the extraction window, instead of being
    sent to fail for no room; a thread the window closes on is "not reached".
(c) Price look-ups go first to the products closest to being ranked: the fewest top weights with no evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.contracts import (
    DataClass,
    Difficulty,
    ExtractInput,
    LLMTask,
    LLMUnavailable,
    Provider,
    ResolvedValue,
    Source,
    ValueState,
)
from app.decide.rules import PRICE_KEY
from app.enrich import price_targets
from app.extract import GuardedExtractStage
from app.extract.schedule import SendWindow, read_in_lanes
from app.extract.settings import ExtractSettings, load_extract_settings
from app.llm import InMemoryCallLog, WrapperLLMClient
from app.llm.config import LLMConfig
from app.llm.errors import AuthFailed
from app.llm.health import ProviderHealth
from app.llm.pool import ATTEMPT_SHARE, ModelPool, time_limit_s
from app.llm.providers import Adapter, RawCompletion
from tests.fakes.research import thread, web_source
from tests.faults.llm.test_cut_off_retry import PAGE, SYSTEM, Answer, _config
from tests.unit.enrich.test_price_lookup import NOVA, VELA, _rubric

TASK = LLMTask.EXTRACT_CLAIMS
LIMIT = time_limit_s(TASK)
OK = '{"product": "Nova Book 14"}'


@dataclass
class TimedAdapter:
    """Answers from `replies` in order (the last repeats), and records the time each attempt was given."""

    provider: Provider
    replies: tuple[str, ...] = (OK,)
    timeouts: list[float] = field(default_factory=list)
    cut: bool = True  # an unusable answer fills its room (cut off); False: a short bad answer

    def call(
        self,
        model: str,
        system: str,
        user: str,
        max_output_tokens: int,
        timeout_s: float,
        extra: Mapping[str, object] | None = None,
    ) -> RawCompletion:
        self.timeouts.append(timeout_s)
        text = self.replies[min(len(self.timeouts), len(self.replies)) - 1]
        size = max_output_tokens if text != OK and self.cut else 10
        return RawCompletion(text=text, input_tokens=100, output_tokens=size)


def _client(
    adapters: Mapping[Provider, TimedAdapter], config: LLMConfig, health: ProviderHealth | None = None
) -> WrapperLLMClient:
    typed: dict[Provider, Adapter] = dict(adapters)
    pool = ModelPool(config.pool, sleep=lambda _: None)
    return WrapperLLMClient(config, typed, InMemoryCallLog(), health=health, limiters={}, pool=pool)


def _public(client: WrapperLLMClient, data_class: DataClass = DataClass.PUBLIC) -> Answer:
    return client.complete(TASK, SYSTEM, PAGE, Answer, data_class=data_class, difficulty=Difficulty.LONG)


# --- (a) the first attempt's time ---------------------------------------------------------------------------------


def test_a_page_with_no_other_model_to_turn_to_gets_the_whole_time() -> None:
    mistral = TimedAdapter(Provider.MISTRAL)
    assert _public(_client({Provider.MISTRAL: mistral}, _config([Provider.MISTRAL]))).product == "Nova Book 14"
    (given,) = mistral.timeouts
    assert ATTEMPT_SHARE * LIMIT < given <= LIMIT - 2  # past the share's 9 s, and still inside the source's 15 s


def test_while_another_model_on_the_route_has_room_the_share_stays() -> None:
    """The fallback the share keeps time for can still be used: nothing changes (the existing fallback tests too)."""
    gemini, mistral = TimedAdapter(Provider.GEMINI), TimedAdapter(Provider.MISTRAL)
    client = _client({Provider.GEMINI: gemini, Provider.MISTRAL: mistral}, _config([Provider.GEMINI, Provider.MISTRAL]))
    _public(client)
    assert gemini.timeouts == [pytest.approx(ATTEMPT_SHARE * LIMIT, abs=0.05)]


def test_a_model_switched_off_is_no_room_so_the_page_gets_the_whole_time() -> None:
    health = ProviderHealth()
    health.record_failure(Provider.MISTRAL, AuthFailed(Provider.MISTRAL, "HTTP 401"))
    gemini, mistral = TimedAdapter(Provider.GEMINI), TimedAdapter(Provider.MISTRAL)
    config = _config([Provider.GEMINI, Provider.MISTRAL])
    _public(_client({Provider.GEMINI: gemini, Provider.MISTRAL: mistral}, config, health))
    assert gemini.timeouts[0] > ATTEMPT_SHARE * LIMIT


def test_only_the_first_attempt_and_only_public_pages_get_the_whole_time() -> None:
    cut = '{"product": "Nova Bo'
    mistral = TimedAdapter(Provider.MISTRAL, replies=(cut, OK))
    _public(_client({Provider.MISTRAL: mistral}, _config([Provider.MISTRAL])))
    first, retry = mistral.timeouts
    assert first > ATTEMPT_SHARE * LIMIT >= retry  # the one retry keeps the share
    groq = TimedAdapter(Provider.GROQ)
    _public(_client({Provider.GROQ: groq}, _config([Provider.GEMINI])), DataClass.REDDIT)  # Reddit's route: Groq
    assert groq.timeouts[0] <= ATTEMPT_SHARE * LIMIT


def test_the_whole_time_is_never_less_than_the_share() -> None:
    """With 7 s left, the share gives 7 s; the whole time less its margin would give 5. The larger goes."""
    from app.llm.usage import TokenMeter

    mistral = TimedAdapter(Provider.MISTRAL)
    client = _client({Provider.MISTRAL: mistral}, _config([Provider.MISTRAL]))
    with TokenMeter(already_spent_s=LIMIT - 7).counting():
        _public(client)
    assert mistral.timeouts[0] == pytest.approx(7, abs=0.2)


def test_a_model_that_gave_up_on_the_call_keeps_no_time_from_the_next() -> None:
    """Gemini's answers are unusable twice, so it is done with this page; Mistral, next, gets the whole time."""
    gemini = TimedAdapter(Provider.GEMINI, replies=("not json",), cut=False)
    mistral = TimedAdapter(Provider.MISTRAL)
    _public(_client({Provider.GEMINI: gemini, Provider.MISTRAL: mistral}, _config([Provider.GEMINI, Provider.MISTRAL])))
    assert len(gemini.timeouts) == 2
    assert mistral.timeouts[0] > ATTEMPT_SHARE * LIMIT


# --- (b) a Reddit thread waits for room ---------------------------------------------------------------------------


OPEN = SendWindow(seconds_left=lambda: 150.0, later_stages_s=59.0, source_s=15.0)


def _reddit() -> Source:
    return thread("t3_madeup1", "Made-up thread about the Nova Buds")


def test_a_reddit_thread_is_sent_only_once_groqs_reddit_lane_has_room() -> None:
    events: list[str] = []
    answers = iter([False, False, True])

    def room(source: Source, within_s: float) -> bool | None:
        events.append("room?")
        return next(answers)

    def read(source: Source) -> str:
        events.append("read")
        return source.id

    sent = read_in_lanes([_reddit()], read, workers=2, window=OPEN, room=room, pause=lambda _: None)
    assert events == ["room?", "room?", "room?", "read"]
    assert sent.not_reached == 0


def test_a_thread_the_window_closes_on_is_not_reached_and_never_sent() -> None:
    left = iter([150.0, 150.0, 150.0, 0.0, 0.0, 0.0, 0.0])
    window = SendWindow(seconds_left=lambda: next(left, 0.0), later_stages_s=59.0, source_s=15.0)
    read: list[str] = []
    sent = read_in_lanes(
        [_reddit()],
        lambda s: read.append(s.id),
        workers=2,
        window=window,
        room=lambda s, w: False,
        pause=lambda _: None,
    )
    assert read == []
    assert sent.not_reached == 1


def test_a_search_snippet_never_waits_for_room() -> None:
    snippet = Source.model_validate({**web_source("https://www.example.com/a").model_dump(), "via": "search_snippet"})
    asked: list[str] = []
    read_in_lanes([snippet], lambda s: s.id, workers=2, window=OPEN, room=lambda s, w: asked.append(s.id) or True)
    assert asked == []


@dataclass
class RoomLLM:
    """An LLM client that only says whether there is room, and records what it was asked."""

    asked: list[tuple[DataClass, int]] = field(default_factory=list)

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        raise LLMUnavailable("not used here")

    def wait_for_room(self, task: LLMTask, data_class: DataClass, sent: int, answer: int, within_s: float) -> bool:
        self.asked.append((data_class, sent + answer))
        return True

    def wait_for_public_room(self, task: LLMTask, sent: int, answer: int, within_s: float) -> bool:
        self.asked.append((DataClass.PUBLIC, sent + answer))
        return True


def _inp(source: Source) -> ExtractInput:
    return ExtractInput(source=source, criterion_keys=["battery_life"], category_label="earbuds")


def test_extraction_asks_groqs_reddit_lane_for_a_thread_and_a_public_provider_for_a_page() -> None:
    llm = RoomLLM()
    stage = GuardedExtractStage(llm, load_extract_settings(env={}))  # type: ignore[arg-type]
    assert stage.room_for("srch_room", _inp(_reddit()), 2.0) is True
    assert stage.room_for("srch_room", _inp(web_source("https://www.example.com/r")), 2.0) is True
    assert [data_class for data_class, _ in llm.asked] == [DataClass.REDDIT, DataClass.PUBLIC]


def test_a_thread_the_searchs_reddit_budget_cant_take_never_waits() -> None:
    """Inside the Reddit lane's budget (US-14): a thread over it goes at once, and run() counts it as over budget."""
    llm = RoomLLM()
    small = ExtractSettings.model_validate(
        {**load_extract_settings(env={}).model_dump(), "reddit_tokens_per_search": 1}
    )
    stage = GuardedExtractStage(llm, small)  # type: ignore[arg-type]
    assert stage.room_for("srch_room", _inp(_reddit()), 2.0) is None
    assert llm.asked == []


def test_the_reddit_room_check_looks_only_at_models_that_never_train() -> None:
    """Build rule 8: with every Groq model switched off, no room is found for Reddit text, though Gemini has some."""
    config = _config([Provider.GEMINI])
    health = ProviderHealth()
    health.record_failure(Provider.GROQ, AuthFailed(Provider.GROQ, "HTTP 401"))
    client = _client({p: TimedAdapter(p) for p in (Provider.GEMINI, Provider.GROQ)}, config, health)
    assert client.room_in(TASK, DataClass.PUBLIC, 500, 500) == 0
    assert client.room_in(TASK, DataClass.REDDIT, 500, 500) is None


# --- (c) the price look-ups' order ----------------------------------------------------------------------------------


def _value(product_id: str, key: str, state: ValueState, claim: str) -> ResolvedValue:
    return ResolvedValue(
        product_id=product_id,
        criterion_key=key,
        value=8.0 if state is not ValueState.UNKNOWN else None,
        state=state,
        confidence=0.8,
        supporting_claim_ids=[claim] if state is not ValueState.UNKNOWN else [],
    )


def test_a_price_goes_first_to_the_product_closest_to_being_ranked() -> None:
    """Nova is talked about more, but has none of the top weights; Vela has them all, so its price matters first."""
    top = ["cpu_performance", "keyboard_comfort", "battery_life"]  # the example rubric's three highest weights
    values = [_value(VELA.id, key, ValueState.KNOWN, f"clm_aaaaaaa{n}") for n, key in enumerate(top)]
    values += [_value(NOVA.id, "weight", ValueState.KNOWN, f"clm_bbbbbbb{n}") for n in range(3)]
    values += [_value(p.id, PRICE_KEY, ValueState.UNKNOWN, "") for p in (VELA, NOVA)]
    source_of = {f"clm_bbbbbbb{n}": f"src_nova_{n}" for n in range(3)} | {
        f"clm_aaaaaaa{n}": "src_vela" for n in range(3)
    }
    aliases = {VELA.name: VELA.id, NOVA.name: NOVA.id}
    targets = price_targets(_rubric(), [NOVA, VELA], values, aliases, source_of)
    assert [t.product_id for t in targets] == [VELA.id, NOVA.id]


def test_the_reddit_room_check_counts_what_the_search_already_sent() -> None:
    """With threads already sent: over the Reddit share, or over the budget with nothing running to free it, never
    waits; under it, or with running calls that may free enough, it waits for Groq's room."""
    base = load_extract_settings(env={})
    real_use = ExtractSettings.model_validate({**base.model_dump(), "book_real_use": True})
    for settings, setup, waits in (
        (base, {"reddit_sent": 10_000}, False),  # the share is taken while the cap has room
        (base, {"reddit_booked": base.reddit_tokens_per_search}, False),  # the budget is spent, nothing frees it
        (
            real_use,
            {"reddit_booked": base.reddit_tokens_per_search, "reddit_running": base.reddit_tokens_per_search},
            True,
        ),
        (base, {}, True),  # room in the share and the budget
    ):
        llm = RoomLLM()
        stage = GuardedExtractStage(llm, settings)  # type: ignore[arg-type]
        counts = stage._for("srch_room3")
        for field_name, value in setup.items():
            setattr(counts, field_name, value)
        answer = stage.room_for("srch_room3", _inp(_reddit()), 2.0)
        assert (answer is True) == waits and bool(llm.asked) == waits, setup
