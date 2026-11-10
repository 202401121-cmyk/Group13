"""Every malformed output, through its parser the way the stage calls it (US-48): canonical shape or clean fallback.

See README.md. A provider changing its format should fail here, not corrupt a ranking.
"""

from __future__ import annotations

import pytest

from .registry import PARSERS, Case, all_cases, holds_a_contract


@pytest.mark.parametrize("case", all_cases(), ids=lambda case: f"{case.parser}/{case.name}")
def test_case_gives_its_expected_outcome(case: Case) -> None:
    parser = PARSERS[case.parser]
    given = case.expected.get("given", {})
    ran = parser.run(case.raw, given)  # no case may raise

    # The wrapper catches anything a parser raises and blames the provider, which would hide a crash from this test.
    assert "error" not in ran.attempts, "a parser raised, and the wrapper counted it against the provider"
    assert ran.attempts or not parser.calls, "the reply was never asked for, so this proves nothing"
    if holds_a_contract(ran.result):
        assert type(ran.result).model_validate_json(ran.result.model_dump_json()) == ran.result

    if case.expected["outcome"] == "canonical":
        assert ran.view == case.expected["object"]
    else:
        assert parser.fallbacks[case.expected["fallback"]](ran, given), f"not {case.expected['fallback']}: {ran.view}"
