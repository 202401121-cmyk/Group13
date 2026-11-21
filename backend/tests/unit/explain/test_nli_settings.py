"""US-22's settings (S5-4): NLI=on|off, config/nli.yaml read with an exact revision, and the model used only when it is
pinned and tuned."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.explain.nli_settings import NliSettings, load_nli_settings, nli_switch

COMMIT = "a150876415327c80daeff35ca6f68f5ed8cf5c24"


def test_nli_is_off_unless_switched_on() -> None:
    """P-48: the check fits only machines with enough memory, and real explanations need the follow-up first."""
    assert nli_switch({}) is False
    assert nli_switch({"NLI": ""}) is False
    assert nli_switch({"NLI": "OFF"}) is False
    assert nli_switch({"NLI": " On "}) is True
    assert NliSettings().enabled is False


def test_an_unknown_value_keeps_it_off_and_says_so(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert nli_switch({"NLI": "maybe"}) is False
    assert "not on or off" in caplog.text


def test_the_repository_config_is_off_by_default_and_on_only_with_nli_on() -> None:
    assert not load_nli_settings(env={}).enabled
    assert load_nli_settings(env={"NLI": "on"}).enabled


def test_the_repository_config_pins_the_revision_and_reads_the_models_labels() -> None:
    settings = load_nli_settings(env={})
    assert settings.repo == "cross-encoder/nli-deberta-v3-xsmall"
    assert settings.revision == COMMIT
    assert (settings.labels, settings.entailment_index) == (3, 1)  # 0 contradiction, 1 entailment, 2 neutral
    assert (settings.premise_tokens, settings.time_limit_s, settings.quoted_words) == (400, 5.0, 6)
    assert settings.reference_tolerance == 0.02


def test_the_repository_config_pins_the_chosen_file_its_hashes_the_tuned_threshold_and_the_reference() -> None:
    """S5-4 DECIDED 1 and 6: written by evals.citation.model_check and evals.citation.run, 21 Nov 2026."""
    settings = load_nli_settings(env={})
    assert settings.pinned
    assert settings.model_file == "onnx/model_O4.onnx"
    assert settings.model_sha256 == "7f3e118f33944fe2a2bfa50ae889098f12914e4fd5194ab13dbaf82a5bee02c5"
    assert settings.tokenizer_sha256 == "5124ef2ead1a10a717703bc436de7f353da76d6340e4587719b42b1693707964"
    assert settings.threshold == 0.9608
    assert len(settings.reference) == 6
    assert all(0.0 <= pair.entailment <= 1.0 for pair in settings.reference)


def test_a_file_that_cant_be_read_gives_no_model(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    bad = tmp_path / "nli.yaml"
    bad.write_text("- not a mapping\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        settings = load_nli_settings(bad, env={"NLI": "off"})
    assert settings == NliSettings(enabled=False)
    assert not settings.pinned
    assert "could not be read" in caplog.text


def test_only_an_exact_revision_both_hashes_a_file_and_a_threshold_pin_it() -> None:
    pinned = NliSettings(
        revision=COMMIT, model_file="onnx/model.onnx", model_sha256="a" * 64, tokenizer_sha256="b" * 64, threshold=0.6
    )
    assert pinned.pinned
    for change in (
        {"revision": "main"},
        {"model_file": ""},
        {"model_sha256": "a" * 63},
        {"tokenizer_sha256": ""},
        {"threshold": None},
        {"entailment_index": 3},
        {"premise_overlap": 400},
    ):
        assert not NliSettings.model_validate({**pinned.model_dump(), **change}).pinned, change
