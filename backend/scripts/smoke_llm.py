"""Live smoke test for every LLM provider that has a key (US-26, US-46). Calls real APIs: never run it in CI or pytest.

Run from backend/:  python -m scripts.smoke_llm

For each provider with its key set (Groq, Gemini, OpenRouter, Cerebras and Mistral), it sends one tiny JSON task
through the same adapter the app uses, with the same settings (Mistral's JSON mode, and MISTRAL_MODEL if set),
and prints a table: provider, model, whether it may train on inputs, works, latency, tokens, and the provider's
rate-limit response headers. Then it prints our own limits (requests per second, tokens per minute and per day).
The prompt is fixed public text, so it is safe to send to every provider.
Keys are read only from the environment (python-dotenv loads .env if there is one) and are never printed.
It is a report, not a gate: it exits 0 even when a provider fails.
"""

from __future__ import annotations

import io
import os
import sys
from dataclasses import dataclass, field
from time import monotonic

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel

from app.llm import load_llm_config
from app.llm.config import LLMConfig, ProviderSettings, with_env_overrides
from app.llm.errors import InvalidOutput, ProviderFailure
from app.llm.output import build_system_prompt, parse_output
from app.llm.providers import OpenAICompatibleAdapter

SYSTEM = "You are a health check for an API client."
USER = 'Reply with exactly this JSON object: {"ok": true}'
MAX_OUTPUT_TOKENS = 512  # generous: some models spend output tokens on reasoning before the reply


class Pong(BaseModel):
    ok: bool


@dataclass
class Row:
    provider: str
    model: str
    trains: str
    works: str
    latency: str = "-"
    tokens: str = "-"
    note: str = ""
    ratelimit_headers: dict[str, str] = field(default_factory=dict)


def check(settings: ProviderSettings, model: str, key: str, http: httpx.Client) -> Row:
    adapter = OpenAICompatibleAdapter(settings.name, settings.base_url, key, http, json_mode=settings.json_mode)
    name, trains = settings.name.value, _yes_no(settings.trains_on_inputs)
    started = monotonic()
    try:
        raw = adapter.call(
            model, build_system_prompt(SYSTEM, Pong), USER, MAX_OUTPUT_TOKENS, settings.request_timeout_s
        )
        parse_output(raw.text, Pong)
    except ProviderFailure as exc:  # the message names the provider and status only, never the key
        return Row(name, model, trains, "no", _ms(started), note=str(exc), ratelimit_headers=exc.ratelimit_headers)
    except InvalidOutput:
        return Row(name, model, trains, "no", _ms(started), note="replied, but not with the expected JSON")
    tokens = f"{raw.input_tokens} in / {raw.output_tokens} out"
    return Row(name, model, trains, "yes", _ms(started), tokens, ratelimit_headers=raw.ratelimit_headers)


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def our_limits(config: LLMConfig) -> list[str]:
    """One line per provider that has limits of our own (US-46)."""
    lines = []
    for p in config.providers:
        if p.requests_per_second is None and p.tokens_per_minute is None and p.daily_tokens is None:
            continue
        lines.append(
            f"  {p.name.value}: {_or_none(p.requests_per_second)} requests/s, "
            f"{_or_none(p.tokens_per_minute)} tokens/min, {_or_none(p.daily_tokens)} tokens/day"
        )
    return lines


def _or_none(value: float | None) -> str:
    if value is None:
        return "no limit"
    return f"{int(value):,}" if float(value).is_integer() else f"{value:g}"


def _ms(started: float) -> str:
    return f"{(monotonic() - started) * 1000:.0f} ms"


def main() -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv()
    config = with_env_overrides(load_llm_config(), os.environ)
    rows: list[Row] = []
    with httpx.Client() as http:
        for settings in config.providers:
            key = os.environ.get(settings.api_key_env, "").strip()
            for limits in (m for m in config.models if m.provider == settings.name):
                if not key:
                    trains = _yes_no(settings.trains_on_inputs)
                    note = f"{settings.api_key_env} not set"
                    rows.append(Row(settings.name.value, limits.model, trains, "skipped", note=note))
                else:
                    rows.append(check(settings, limits.model, key, http))

    print(f"{'provider':<11} {'model':<40} {'trains':<7} {'works':<8} {'latency':<9} {'tokens':<18} note")
    for r in rows:
        print(f"{r.provider:<11} {r.model:<40} {r.trains:<7} {r.works:<8} {r.latency:<9} {r.tokens:<18} {r.note}")
    own_limits = our_limits(config)
    if own_limits:
        print("\nOur own limits (models.yaml, or MISTRAL_RPS / MISTRAL_TPM / MISTRAL_DAILY_TOKENS in .env):")
        print("\n".join(own_limits))
    for r in rows:
        if r.ratelimit_headers:
            print(f"\n{r.provider} rate-limit headers:")
            for name, value in sorted(r.ratelimit_headers.items()):
                print(f"  {name}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
