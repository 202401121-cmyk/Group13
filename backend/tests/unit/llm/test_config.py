"""config/models.yaml loads and its rules hold (US-26, US-46)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from app.contracts import ModelLimits, Provider
from app.llm.config import DEFAULT_CONFIG_PATH, LLMConfigError, load_llm_config


def _raw() -> dict[str, object]:
    data: dict[str, object] = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    return data


def _write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_the_real_config_loads() -> None:
    config = load_llm_config()
    assert [p.name for p in config.providers] == [
        Provider.GROQ,
        Provider.GEMINI,
        Provider.OPENROUTER,
        Provider.CEREBRAS,
        Provider.MISTRAL,
    ]
    assert all(isinstance(m, ModelLimits) for m in config.models)
    assert all(p.base_url.startswith("https://") for p in config.providers)


def test_every_provider_has_a_model() -> None:
    config = load_llm_config()
    assert {m.provider for m in config.models} == set(Provider)


def test_gemini_is_marked_as_training_on_the_free_tier() -> None:
    gemini = [m for m in load_llm_config().models if m.provider == Provider.GEMINI]
    assert gemini and all(m.trains_on_free_tier for m in gemini)


def test_the_trains_on_inputs_flags_match_the_terms_we_read() -> None:
    config = load_llm_config()
    trains = {p.name: p.trains_on_inputs for p in config.providers}
    assert trains == {
        Provider.GROQ: False,
        Provider.CEREBRAS: False,
        Provider.GEMINI: True,
        Provider.OPENROUTER: True,
        Provider.MISTRAL: True,
    }
    assert all(m.trains_on_free_tier == trains[m.provider] for m in config.models)
    assert all(p.terms_url.startswith("https://") and p.terms_checked.year >= 2026 for p in config.providers)


def test_a_model_flag_that_disagrees_with_its_provider_fails(tmp_path: Path) -> None:
    data = _raw()
    for row in data["models"]:  # type: ignore[attr-defined]
        if row["provider"] == "mistral":
            row["trains_on_free_tier"] = False
    with pytest.raises(LLMConfigError, match="must match the provider's trains_on_inputs"):
        load_llm_config(_write(tmp_path, data))


def test_a_provider_without_its_terms_fails(tmp_path: Path) -> None:
    data = _raw()
    del data["providers"][0]["trains_on_inputs"]  # type: ignore[index]
    with pytest.raises(LLMConfigError, match="trains_on_inputs"):
        load_llm_config(_write(tmp_path, data))


def test_missing_provider_fails(tmp_path: Path) -> None:
    data = _raw()
    data["providers"] = [p for p in data["providers"] if p["name"] != "cerebras"]  # type: ignore[attr-defined]
    with pytest.raises(LLMConfigError, match="no provider entry for"):
        load_llm_config(_write(tmp_path, data))


def test_provider_without_a_model_fails(tmp_path: Path) -> None:
    data = _raw()
    data["models"] = [m for m in data["models"] if m["provider"] != "openrouter"]  # type: ignore[attr-defined]
    with pytest.raises(LLMConfigError, match="at least one model"):
        load_llm_config(_write(tmp_path, data))


def test_http_url_fails(tmp_path: Path) -> None:
    data = _raw()
    data["providers"][0]["base_url"] = "http://api.groq.com/openai/v1"  # type: ignore[index]
    with pytest.raises(LLMConfigError, match="https"):
        load_llm_config(_write(tmp_path, data))


def test_duplicate_provider_fails(tmp_path: Path) -> None:
    data = _raw()
    data["providers"].append(data["providers"][0])  # type: ignore[attr-defined, index]
    with pytest.raises(LLMConfigError, match="only once"):
        load_llm_config(_write(tmp_path, data))


def test_duplicate_model_fails(tmp_path: Path) -> None:
    data = _raw()
    data["models"].append(data["models"][0])  # type: ignore[attr-defined, index]
    with pytest.raises(LLMConfigError, match="only once"):
        load_llm_config(_write(tmp_path, data))


def test_gemini_cannot_claim_it_does_not_train(tmp_path: Path) -> None:
    data = _raw()
    for row in data["models"]:  # type: ignore[attr-defined]
        if row["provider"] == "gemini":
            row["trains_on_free_tier"] = False
    with pytest.raises(LLMConfigError, match="build rule 8"):
        load_llm_config(_write(tmp_path, data))


def test_missing_file_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(LLMConfigError, match="cannot read"):
        load_llm_config(tmp_path / "nope.yaml")


def test_provider_lookup() -> None:
    assert load_llm_config().provider(Provider.CEREBRAS).api_key_env == "CEREBRAS_API_KEY"
