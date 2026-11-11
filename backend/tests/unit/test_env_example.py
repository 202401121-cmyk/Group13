"""A .env copied from .env.example says what the example says.

python-dotenv before 1.2.4 read "KEY=   # note" as the value "# note", not as an empty value. In a copied .env that
made an unset API key look set, turned the Reddit salt into text anyone can read in the repository, and stopped the LLM
settings from loading ("MISTRAL_RPS must be a number"). pyproject.toml now asks for 1.2.4 or newer, and every comment in
the example sits on its own line, which any reader of the file takes the same way.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import dotenv_values, load_dotenv

from app.contracts import Provider
from app.llm import load_llm_config
from app.llm.config import with_env_overrides

EXAMPLE = Path(__file__).resolve().parents[3] / ".env.example"
SECRET_ENDINGS = ("_KEY", "_SECRET", "_SALT", "_TOKEN")


def _values() -> dict[str, str]:
    return {key: value or "" for key, value in dotenv_values(EXAMPLE).items()}


def test_no_value_in_the_example_is_a_comment() -> None:
    values = _values()
    assert len(values) >= 40
    assert [key for key, value in values.items() if "#" in value] == []


def test_every_secret_in_the_example_is_empty() -> None:
    values = _values()
    secrets = [key for key in values if key.endswith(SECRET_ENDINGS) or key == "DATABASE_URL"]
    assert {"GROQ_API_KEY", "REDDIT_USER_SALT", "DATABASE_URL"} <= set(secrets)
    assert {key: values[key] for key in secrets} == dict.fromkeys(secrets, "")


def test_tests_never_read_a_dotenv_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """tests/conftest.py sets PYTHON_DOTENV_DISABLED; python-dotenv 1.2 or newer honours it (pyproject.toml)."""
    env = tmp_path / ".env"
    env.write_text("SHOPSENSE_DOTENV_PROBE=from-a-file\n", encoding="utf-8")
    monkeypatch.delenv("SHOPSENSE_DOTENV_PROBE", raising=False)
    assert load_dotenv(env) is False
    assert "SHOPSENSE_DOTENV_PROBE" not in os.environ


def test_the_llm_settings_load_from_a_copy_of_the_example() -> None:
    mistral = with_env_overrides(load_llm_config(), _values()).provider(Provider.MISTRAL)
    assert (mistral.requests_per_minute, mistral.requests_per_second, mistral.tokens_per_minute) == (2, None, 40_000)
    assert mistral.daily_tokens == 1_000_000
