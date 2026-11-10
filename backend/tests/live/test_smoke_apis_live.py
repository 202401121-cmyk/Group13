"""The all-services smoke command against the real services (W2). Never runs in CI or a plain test run.

Run it on purpose, from backend/:
    RUN_LIVE_TESTS=1 pytest -m live tests/live/test_smoke_apis_live.py -q

It skips Serper (that costs a free query) and checks the command itself: one row per service and no key in the
output. Whether each provider is up today is in the table it prints, not in this test's result.
"""

from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv

from scripts.smoke_apis import KEY_NAMES, SERVICES, main

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("RUN_LIVE_TESTS") != "1", reason="live: set RUN_LIVE_TESTS=1 to run"),
]


def test_the_real_run_reports_every_service_and_never_a_key(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(
        "PYTHON_DOTENV_DISABLED", raising=False
    )  # this live test reads the real keys (tests/conftest.py)
    load_dotenv()
    main([])
    out = capsys.readouterr().out
    for service in SERVICES:
        assert any(line.split()[:1] == [service] for line in out.splitlines()), service
    for name in KEY_NAMES:
        value = (os.environ.get(name) or "").strip()
        if len(value) >= 6:
            assert value not in out, name
