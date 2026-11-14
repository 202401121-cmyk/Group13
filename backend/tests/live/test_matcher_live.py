"""Live check of the trained matcher (US-15, S3-15): the real model, from Hugging Face or the local cache.

Run it on purpose, from backend/:
    RUN_LIVE_TESTS=1 pytest -m live tests/live/test_matcher_live.py -q -s

The first run downloads model.onnx (91 MB) and tokenizer.json at the pinned revision into backend/.cache/matcher/;
later runs read them from there. No key is sent and no other service is called. It prints the load time, the time for
1,000 pairs and for three sets of 1,000 mentions (in one of them the model decides about 780 pairs), the method each
run used, and the process's peak memory, for ml/MODELS.md card 1. A failure here means the model can't be had or is
too slow, not that the code is wrong: the offline tests cover the code.
"""

from __future__ import annotations

import os
import random
import sys
import time

import pytest

from app.contracts import MatchInput, ProductMention, StageContext
from app.entities import ModelMatchStage
from app.entities.model import ModelHolder, load_model_scorer
from app.entities.normalise import parse_name
from app.entities.settings import load_matcher_settings

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("RUN_LIVE_TESTS") != "1", reason="live: set RUN_LIVE_TESTS=1 to run"),
]


def _peak_memory_mb() -> float:
    """The process's peak resident memory so far, in MB (10^6 bytes), with no extra package."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        current = ctypes.windll.kernel32.GetCurrentProcess
        current.restype = wintypes.HANDLE  # a pseudo-handle: cut to 32 bits by the default int, the call fails
        info = ctypes.windll.psapi.GetProcessMemoryInfo
        info.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        info.restype = wintypes.BOOL
        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        if not info(current(), ctypes.byref(counters), counters.cb):
            raise OSError("GetProcessMemoryInfo failed")
        return float(counters.PeakWorkingSetSize) / 1e6
    import resource

    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024 / 1e6  # Linux gives KiB


def _realistic() -> list[str]:
    rng = random.Random(7)
    brands = ["Sony", "Samsung", "Apple", "Dell", "Asus", "Lenovo", "HP", "Acer", "Bose", "JBL"]
    return [
        f"{rng.choice(brands)} {rng.choice(['Nova', 'Orion', 'Vela', 'Aurora'])} "
        f"{rng.choice('ABCGXZ')}{rng.randint(1, 400)} {rng.choice(['', '8GB', '16GB 512GB', 'Black'])}".strip()
        for _ in range(1000)
    ]


def _one_shared_word() -> list[str]:
    rng = random.Random(11)
    syllables = ["ka", "lo", "mi", "ra", "su", "te", "vo", "ze", "ni", "pa"]
    return sorted({"Nova " + "".join(rng.choice(syllables) for _ in range(4)) for _ in range(1400)})[:1000]


def test_the_real_model_loads_and_matches_1000_mentions_in_under_5_seconds() -> None:
    settings = load_matcher_settings(env={"MATCHER": "model"})
    before = _peak_memory_mb()
    started = time.perf_counter()
    scorer = load_model_scorer(settings)
    load_s = time.perf_counter() - started
    print(f"\nload {load_s:.2f} s (peak memory {before:.0f} MB before, {_peak_memory_mb():.0f} MB after)")

    pairs = [
        (parse_name(a).base, parse_name(b).base)
        for a, b in [
            ("Kindle Paperwhite", "Kindle Paperwite"),
            ("Nova Book 14 16GB 512GB silver laptop", "Nova Book 14 Laptop 16 GB / 512 GB SSD"),
            ("Bose QuietComfort Ultra", "Bose QuietComfort Ultra Headphones"),
            ("Kettle Pro 1.7L Steel", "Toaster Max 2 Slice"),
        ]
    ]
    scores = scorer.score(pairs)
    assert all(0.0 <= s <= 1.0 for s in scores)
    assert scorer.score(pairs) == scores  # the same pairs, the same scores
    print("scores: " + ", ".join(f"{a!r}~{b!r} {s:.4f}" for (a, b), s in zip(pairs, scores, strict=True)))

    thousand = (pairs * 250)[:1000]
    started = time.perf_counter()
    scorer.score(thousand)
    print(f"1,000 pairs: {time.perf_counter() - started:.2f} s, batches of {settings.batch_size}")

    holder = ModelHolder(settings, load=lambda s: scorer)
    stage = ModelMatchStage(settings=settings, model=holder)
    repeats = [name for name in _one_shared_word()[:40] for _ in range(25)]  # about 780 pairs only the model decides
    sets = (("realistic", _realistic()), ("40 names, 25 times each", repeats), ("one shared word", _one_shared_word()))
    for name, names in sets:
        inp = MatchInput(mentions=[ProductMention(raw_name=n, is_product=True) for n in names])
        started = time.perf_counter()
        result = stage.run(inp, StageContext(search_id="srch_aaaaaaaaaaaaaaaaaaaaaaaa"))
        seconds = time.perf_counter() - started
        print(f"{name}: 1,000 mentions in {seconds:.2f} s, method {result.method}, {len(result.products)} products")
        assert seconds < 5.0
        if names is repeats:
            assert result.method == "model"  # the model decided every open pair, inside its share
    print(f"peak memory {_peak_memory_mb():.0f} MB (the whole test process)")
