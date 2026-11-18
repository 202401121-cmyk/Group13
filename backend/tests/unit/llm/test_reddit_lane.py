"""US-46 (S4-31, D-76 item 4): Reddit-class extraction (Reddit and YouTube comments) may use at most half of each Groq
model's tokens a minute, so public pages always find Groq room as their last resort. Fake clock; no provider is called.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.contracts import DataClass, Difficulty, LLMTask, ModelLimits
from app.llm import load_llm_config
from app.llm.config import LLMConfig, PoolSettings
from app.llm.pool import ModelPool
from tests.unit.llm.test_pool import BIG, QWEN, SMALL, Answer, Clock, GroqFake, _client, _config, _member, _outcomes

TPM = 8000
HALF = TPM // 2


def _settings(**pool: object) -> PoolSettings:
    config = _config(tokens_per_minute=TPM, requests_per_minute=100)
    return PoolSettings.model_validate({**config.pool.model_dump(), "reddit_minute_share": 0.5, **pool})


def _pool(clock: Clock, **pool: object) -> ModelPool:
    return ModelPool(_settings(**pool), clock=clock, sleep=clock.sleep, today=lambda: date(2026, 11, 18))


def _model(name: str = BIG) -> ModelLimits:
    return next(m for m in _config().models if m.model == name)


def test_the_share_is_a_quarter_in_config() -> None:
    assert load_llm_config().pool.reddit_minute_share == 0.25  # a quarter: Om's choice on 18 Nov 2026


def test_reddit_calls_use_at_most_half_a_members_minute() -> None:
    clock, model = Clock(), _model()
    pool = _pool(clock)
    assert pool.try_take(model, 3000, data_class=DataClass.REDDIT) is not None
    assert pool.try_take(model, 1500, data_class=DataClass.REDDIT) is None  # 4,500 would pass the half
    assert pool.try_take(model, 1000, data_class=DataClass.REDDIT) is not None  # exactly the half


def test_a_public_page_still_finds_room_after_reddit_took_its_half() -> None:
    clock, model = Clock(), _model()
    pool = _pool(clock)
    assert pool.try_take(model, HALF, data_class=DataClass.REDDIT) is not None
    assert pool.try_take(model, 1, data_class=DataClass.REDDIT) is None
    assert pool.try_take(model, 3500, data_class=DataClass.PUBLIC) is not None


def test_both_lanes_get_sends_in_one_minute() -> None:
    """A burst of Reddit calls at one moment, then a public page at the same moment: both are sent."""
    clock = Clock()
    pool = _pool(clock)
    sent = {DataClass.REDDIT: 0, DataClass.PUBLIC: 0}
    for model in (_model(BIG), _model(SMALL), _model(QWEN)):
        for _ in range(10):
            if pool.try_take(model, 1500, data_class=DataClass.REDDIT) is not None:
                sent[DataClass.REDDIT] += 1
        if pool.try_take(model, 1500, data_class=DataClass.PUBLIC) is not None:
            sent[DataClass.PUBLIC] += 1
    assert sent == {DataClass.REDDIT: 6, DataClass.PUBLIC: 3}  # 2 Reddit calls a member (3,000 of 4,000), then a page


def test_without_the_lane_reddit_would_leave_public_pages_no_room() -> None:
    """The same burst with the lane at the whole minute: the share is what keeps a page's room."""
    clock = Clock()
    pool = _pool(clock, reddit_minute_share=1.0)
    model = _model()
    while pool.try_take(model, 1500, data_class=DataClass.REDDIT) is not None:
        pass
    assert pool.try_take(model, 1500, data_class=DataClass.PUBLIC) is None


def test_a_full_reddit_source_bigger_than_the_half_may_go_when_the_lane_is_idle() -> None:
    """A full source books about 5,100 tokens, more than half of 8,000. Alone in its minute it may still go, or no
    Reddit source could ever be read; then nothing more of the lane fits until that minute has passed."""
    clock, model = Clock(), _model()
    pool = _pool(clock)
    assert pool.try_take(model, 5100, data_class=DataClass.REDDIT) is not None
    assert pool.try_take(model, 100, data_class=DataClass.REDDIT) is None
    wait = pool.seconds_until_room(model, 100, data_class=DataClass.REDDIT)
    assert wait is not None and 59 < wait <= 60
    clock.now += 60
    assert pool.try_take(model, 100, data_class=DataClass.REDDIT) is not None


def test_private_work_is_not_held_by_the_lane() -> None:
    clock, model = Clock(), _model()
    pool = _pool(clock)
    assert pool.try_take(model, HALF, data_class=DataClass.REDDIT) is not None
    assert pool.try_take(model, 3000, data_class=DataClass.PRIVATE) is not None  # the shopper's own calls


def test_real_use_frees_the_lanes_room_too() -> None:
    clock, model = Clock(), _model()
    pool = _pool(clock, book_real_use=True)
    booking = pool.try_take(model, 3500, data_class=DataClass.REDDIT)
    assert booking is not None
    assert pool.try_take(model, 3000, data_class=DataClass.REDDIT) is None
    pool.book_real_use(model, booking, 500)  # the call is over and used 500
    assert pool.try_take(model, 3000, data_class=DataClass.REDDIT) is not None


def test_a_running_reddit_call_may_free_the_lanes_room() -> None:
    clock, model = Clock(), _model()
    pool = _pool(clock)
    assert pool.try_take(model, 3500, data_class=DataClass.REDDIT) is not None
    assert pool.running_calls_may_free_room(model, 3000, data_class=DataClass.REDDIT)


def test_the_client_keeps_reddit_extraction_to_the_lane_and_still_sends_the_page() -> None:
    """Through the wrapper: with every member's lane spent, a Reddit source is not sent, and a public page is."""
    clock = Clock()
    base = _config(tokens_per_minute=TPM, requests_per_minute=100)
    config = LLMConfig.model_validate({**base.model_dump(), "pool": _settings().model_dump()})
    fake = GroqFake()
    client = _client(fake, clock, config)
    for name in (BIG, SMALL, QWEN):
        assert client.pool.try_take(_member(client, name), HALF, data_class=DataClass.REDDIT) is not None
    for data_class in (DataClass.REDDIT, DataClass.PUBLIC):
        try:
            client.complete(
                LLMTask.EXTRACT_CLAIMS,
                "Extract claims.",
                "Nova Book 14 is great.",
                Answer,
                data_class=data_class,
                difficulty=Difficulty.LONG,
                max_output_tokens=1000,
            )
        except Exception:  # the Reddit source finds no room and gives up, as extraction does
            assert data_class is DataClass.REDDIT
    outcomes = [outcome for _, outcome in _outcomes(client)]
    assert outcomes.count("ok") == 1  # the public page
    assert len(fake.calls) == 1


@pytest.mark.parametrize("share", [0, -0.5, 1.01])
def test_the_share_must_be_above_0_and_at_most_1(share: float) -> None:
    with pytest.raises(ValidationError):
        _settings(reddit_minute_share=share)


def test_without_real_use_booking_the_lane_keeps_nothing_to_rebook() -> None:
    clock, model = Clock(), _model()
    pool = _pool(clock, book_real_use=False)
    for _ in range(3):
        assert pool.try_take(model, 1000, data_class=DataClass.REDDIT) is not None
    assert pool._lane_bookings == {}  # nothing would ever take them out again


def test_at_real_sizes_a_short_thread_and_a_page_both_go_in_one_minute() -> None:
    """The shipped config, the shopper's 2,000 kept free: a short thread (about 2,960) goes on an empty lane, a second
    Reddit call waits, and a public page as Groq's last resort (up to 3,000 with its answer) still finds room."""
    clock, model = Clock(), _model()
    shipped = load_llm_config().pool
    pool = ModelPool(shipped, clock=clock, sleep=clock.sleep, today=lambda: date(2026, 11, 18))
    reserve = shipped.extract_reserve_tokens_per_minute
    assert pool.try_take(model, 2960, reserve, data_class=DataClass.REDDIT) is not None
    assert pool.try_take(model, 100, reserve, data_class=DataClass.REDDIT) is None
    assert pool.try_take(model, 3000, reserve, data_class=DataClass.PUBLIC) is not None
