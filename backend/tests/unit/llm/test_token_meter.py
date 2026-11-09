"""What a block of LLM calls really used, for a stage that keeps its own budget (US-14)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.llm.usage import TokenMeter, record_use


def test_a_meter_counts_only_inside_its_block() -> None:
    meter = TokenMeter()
    record_use(5)
    with meter.counting():
        record_use(100)
        record_use(20)
    record_use(5)
    assert (meter.attempts, meter.tokens) == (2, 120)


def test_with_no_meter_a_use_goes_nowhere() -> None:
    record_use(100)


def test_an_inner_meter_ends_and_the_outer_one_carries_on() -> None:
    outer, inner = TokenMeter(), TokenMeter()
    with outer.counting():
        with inner.counting():
            record_use(1)
        record_use(2)
    assert (outer.tokens, inner.tokens) == (2, 1)


def test_meters_on_different_threads_stay_apart() -> None:
    def use(tokens: int) -> int:
        meter = TokenMeter()
        with meter.counting():
            for _ in range(200):
                record_use(tokens)
        return meter.tokens

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(use, (1, 2, 3, 4))) == [200, 400, 600, 800]
