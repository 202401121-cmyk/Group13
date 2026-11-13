"""config/models.yaml loads and its rules hold (US-26, US-46)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from app.contracts import DataClass, ModelLimits, Provider
from app.llm import build_llm_client
from app.llm.config import DEFAULT_CONFIG_PATH, LLMConfigError, load_llm_config, with_env_overrides
from app.llm.providers import OpenAICompatibleAdapter


def _raw() -> dict[str, object]:
    data: dict[str, object] = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    return data


def _write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_the_pool_holds_the_three_groq_models_with_their_own_limits() -> None:
    pool = load_llm_config().pool
    assert [m.model for m in pool.members] == ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"]
    for member in pool.members:
        assert member.provider is Provider.GROQ
        assert (member.requests_per_minute, member.tokens_per_minute) == (30, 8000)
        assert (member.tokens_per_day, member.requests_per_day) == (200_000, 1000)
    assert pool.limits_checked.isoformat() == "2026-11-07"
    assert pool.combined_tokens_per_minute == 24_000
    assert pool.reddit_tokens_per_search == 18_000  # 75% of a minute: see docs/api-setup.md


def test_deepseek_takes_public_text_only_and_is_in_public_routes_only() -> None:
    """D-63: DeepSeek may train on inputs, so public text only. S3-0 added it switched off; S3-1 switches it on."""
    config = load_llm_config()
    deepseek = config.provider(Provider.DEEPSEEK)
    assert deepseek.trains_on_inputs is True
    assert deepseek.rotates is False
    assert deepseek.daily_tokens == 500_000
    assert deepseek.api_key_env == "DEEPSEEK_API_KEY"
    assert deepseek.available is True and deepseek.unavailable_reason is None
    assert [m.trains_on_free_tier for m in config.models if m.provider is Provider.DEEPSEEK] == [True]
    for data_class, by_difficulty in config.routes.items():
        for chain in by_difficulty.values():
            assert (Provider.DEEPSEEK in chain) is (data_class is DataClass.PUBLIC)


def test_cerebras_is_unavailable_but_keeps_its_flag() -> None:
    cerebras = load_llm_config().provider(Provider.CEREBRAS)
    assert not cerebras.available and "402" in (cerebras.unavailable_reason or "")
    assert cerebras.trains_on_inputs is False


def test_the_loader_refuses_an_unavailable_provider_without_a_reason(tmp_path: Path) -> None:
    data = _raw()
    data["providers"][3].pop("unavailable_reason")  # type: ignore[index]
    with pytest.raises(LLMConfigError, match="unavailable_reason"):
        load_llm_config(_write(tmp_path, data))


@pytest.mark.parametrize("data_class", ["private", "reddit"])
def test_the_loader_refuses_an_unavailable_provider_on_a_private_or_reddit_route(
    tmp_path: Path, data_class: str
) -> None:
    data = _raw()
    data["routes"][data_class]["short"] = ["groq", "cerebras"]  # type: ignore[index]
    with pytest.raises(LLMConfigError, match="unavailable provider"):
        load_llm_config(_write(tmp_path, data))


def test_the_loader_refuses_a_pool_member_with_no_model_row(tmp_path: Path) -> None:
    data = _raw()
    data["pool"]["members"][0]["model"] = "openai/not-a-model"  # type: ignore[index]
    with pytest.raises(LLMConfigError, match="needs a row in models"):
        load_llm_config(_write(tmp_path, data))


def test_the_loader_refuses_a_repeated_pool_member(tmp_path: Path) -> None:
    data = _raw()
    data["pool"]["members"].append(data["pool"]["members"][0])  # type: ignore[index]
    with pytest.raises(LLMConfigError, match="only once"):
        load_llm_config(_write(tmp_path, data))


def test_the_loader_refuses_a_reddit_budget_above_a_minute_of_the_pool(tmp_path: Path) -> None:
    data = _raw()
    data["pool"]["reddit_tokens_per_search"] = 24_001  # type: ignore[index]
    with pytest.raises(LLMConfigError, match="one minute"):
        load_llm_config(_write(tmp_path, data))


def test_config_refuses_a_reserve_that_leaves_no_room(tmp_path: Path) -> None:
    data = _raw()
    data["pool"]["extract_reserve_tokens_per_minute"] = 8000  # type: ignore[index]
    data["pool"]["reddit_tokens_per_search"] = 1  # type: ignore[index]
    with pytest.raises(LLMConfigError, match="room for bulk work"):
        load_llm_config(_write(tmp_path, data))


def test_config_refuses_a_reddit_budget_above_the_room(tmp_path: Path) -> None:
    data = _raw()
    data["pool"]["reddit_tokens_per_search"] = 18_001  # type: ignore[index]
    with pytest.raises(LLMConfigError, match="above the reserve"):
        load_llm_config(_write(tmp_path, data))


def test_a_reddit_budget_filling_the_room_exactly_is_allowed(tmp_path: Path) -> None:
    data = _raw()
    data["pool"]["reddit_tokens_per_search"] = 18_000  # type: ignore[index]
    assert load_llm_config(_write(tmp_path, data)).pool.reddit_tokens_per_search == 18_000


def test_config_refuses_a_negative_reserve(tmp_path: Path) -> None:
    data = _raw()
    data["pool"]["extract_reserve_tokens_per_minute"] = -1  # type: ignore[index]
    with pytest.raises(LLMConfigError):
        load_llm_config(_write(tmp_path, data))


def test_gemini_has_its_thinking_as_low_as_it_goes() -> None:
    """Its thinking counts against the output limit: with a limit of 1,200, a reply was once cut off at 43 tokens."""
    config = load_llm_config()
    assert config.provider(Provider.GEMINI).reasoning_effort == "minimal"
    others = [p.name for p in config.providers if p.reasoning_effort is not None and p.name is not Provider.GEMINI]
    assert others == []


def test_the_client_hands_the_setting_to_the_provider() -> None:
    client = build_llm_client(env={"GEMINI_API_KEY": "a-made-up-key", "GROQ_API_KEY": "a-made-up-key"})
    gemini, groq = client.adapters[Provider.GEMINI], client.adapters[Provider.GROQ]
    assert isinstance(gemini, OpenAICompatibleAdapter) and gemini.reasoning_effort == "minimal"
    assert isinstance(groq, OpenAICompatibleAdapter) and groq.reasoning_effort is None


def test_env_overrides_keep_the_pool() -> None:
    config = with_env_overrides(load_llm_config(), {"MISTRAL_RPS": "1"})
    assert config.pool == load_llm_config().pool


def test_the_real_config_loads() -> None:
    config = load_llm_config()
    assert [p.name for p in config.providers] == [
        Provider.GROQ,
        Provider.GEMINI,
        Provider.OPENROUTER,
        Provider.CEREBRAS,
        Provider.MISTRAL,
        Provider.DEEPSEEK,
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
        Provider.DEEPSEEK: True,  # its privacy policy, read 11 Nov 2026 (D-63)
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


def test_mistral_limits_come_from_models_yaml() -> None:
    mistral = load_llm_config().provider(Provider.MISTRAL)
    assert mistral.json_mode
    limits = (mistral.requests_per_minute, mistral.tokens_per_minute, mistral.daily_tokens)
    assert limits == (2, 40_000, 1_000_000)  # its replies' headers, 10 Nov 2026
    groq = load_llm_config().provider(Provider.GROQ)
    assert (groq.json_mode, groq.requests_per_second, groq.daily_tokens) == (False, None, 600_000)


def test_env_overrides_change_only_what_is_set() -> None:
    config = load_llm_config()
    assert with_env_overrides(config, {}) == config
    assert with_env_overrides(config, {"MISTRAL_MODEL": "  ", "MISTRAL_RPS": ""}) == config
    changed = with_env_overrides(config, {"MISTRAL_DAILY_TOKENS": "250000", "MISTRAL_MODEL": "ministral-3b-latest"})
    assert changed.provider(Provider.MISTRAL).daily_tokens == 250_000
    assert changed.provider(Provider.MISTRAL).requests_per_minute == 2  # unchanged
    mistral_models = [m for m in changed.models if m.provider is Provider.MISTRAL]
    assert [m.model for m in mistral_models] == ["ministral-3b-latest"]
    assert mistral_models[0].context_window_tokens == 262144  # the limits stay with the row


def test_mistral_rpm_sets_its_requests_a_minute() -> None:
    changed = with_env_overrides(load_llm_config(), {"MISTRAL_RPM": "5"}).provider(Provider.MISTRAL)
    assert (changed.requests_per_minute, changed.tokens_per_minute) == (5, 40_000)


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"MISTRAL_RPM": "0"}, "MISTRAL_RPM"),
        ({"MISTRAL_RPS": "fast"}, "MISTRAL_RPS must be a number"),
        ({"MISTRAL_DAILY_TOKENS": "1.5e6"}, "MISTRAL_DAILY_TOKENS must be a number"),
        ({"MISTRAL_TPM": "0"}, "MISTRAL_TPM"),
        ({"MISTRAL_DAILY_TOKENS": "-5"}, "MISTRAL_DAILY_TOKENS"),
    ],
)
def test_a_bad_env_override_fails_clearly(env: dict[str, str], message: str) -> None:
    with pytest.raises(LLMConfigError, match=message):
        with_env_overrides(load_llm_config(), env)
