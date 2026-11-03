"""One smoke test for every external service ShopSense uses (W2). Calls real APIs: never run it in CI or pytest.

Run from backend/:
    python -m scripts.smoke_apis                       every configured service except Serper
    python -m scripts.smoke_apis --only groq,gemini    just these services
    python -m scripts.smoke_apis --include serper      also Serper (each run uses one of its 2,500 free queries)

Each check is the cheapest call that proves the key works:
- LLMs (Groq, Gemini, OpenRouter, Cerebras, Mistral): one call through the wrapper's own provider client and
  models.yaml settings, with the fixed public prompt "Reply with the single word OK." and 5 output tokens; it passes
  if the reply contains OK. A reasoning model can spend those 5 tokens thinking and reply with no text; only then is
  the call repeated once with a 200-token cap, and the note says so.
- Serper: one search, only with --include serper. YouTube: videos.list for one public video (1 quota unit; never
  search.list). Reddit: only when its client ID and secret are set, an app-only OAuth token and one listing with
  limit=1. Jina Reader: read https://example.com (with JINA_API_KEY if set; it also works without a key).
  Hugging Face: whoami, which also shows whether the token is read-only.

A service without a key is "skipped (not configured)", never a failure. Each service gets at most 20 seconds, and
one failing never stops the others. Exit 0 if every configured service passed, 1 if any failed.
Keys come only from the environment (python-dotenv loads the root .env) and are never printed or logged: notes
carry HTTP statuses and error types only, and any key value that slipped into the output would be masked.
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from time import monotonic

import httpx
from dotenv import load_dotenv

from app.contracts import Provider
from app.llm import load_llm_config
from app.llm.config import with_env_overrides
from app.llm.errors import ProviderError, ProviderFailure
from app.llm.providers import OpenAICompatibleAdapter

TIMEOUT_S = 20.0
SYSTEM = "You are a health check for an API client."
PROMPT = "Reply with the single word OK."
MAX_OUTPUT_TOKENS = 5
REASONING_OUTPUT_TOKENS = 200  # only when a reasoning model used all 5 tokens thinking and replied with nothing
PUBLIC_VIDEO_ID = "jNQXAC9IVRw"  # "Me at the zoo", the first video on YouTube: public and stable
JINA_TEST_PAGE = "https://example.com"
REDDIT_LISTING = "https://oauth.reddit.com/r/laptops/new"

LLM_SERVICES = ("groq", "gemini", "openrouter", "cerebras", "mistral")
SERVICES = (*LLM_SERVICES, "serper", "reddit", "youtube", "jina", "huggingface")
OPT_IN = {"serper": "pass --include serper to run it (each run uses one of 2,500 free queries)"}
KEY_NAMES = (
    "GROQ_API_KEY",
    "GEMINI_API_KEY",
    "OPENROUTER_API_KEY",
    "CEREBRAS_API_KEY",
    "MISTRAL_API_KEY",
    "SERPER_API_KEY",
    "REDDIT_CLIENT_ID",
    "REDDIT_CLIENT_SECRET",
    "YOUTUBE_API_KEY",
    "JINA_API_KEY",
    "HF_TOKEN",
)
STATUS_HINTS = {
    401: "key missing or wrong",
    402: "the account needs a plan or credits",
    403: "key not allowed, or quota used up",
    404: "not found",
    429: "rate limited",
}


@dataclass(frozen=True)
class Outcome:
    status: str  # ok, failed or skipped
    note: str = ""
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Row:
    service: str
    status: str
    latency_ms: int | None
    note: str
    headers: dict[str, str] = field(default_factory=dict)


Check = Callable[[Mapping[str, str], httpx.Client], Outcome]


def _key(env: Mapping[str, str], name: str) -> str:
    return (env.get(name) or "").strip()


def _not_configured(*names: str) -> Outcome:
    return Outcome("skipped", f"not configured ({' and '.join(names)} not set)")


def _http_failure(response: httpx.Response) -> Outcome | None:
    """None when the answer is a success; otherwise the status only, never the body (it may echo the key)."""
    if 200 <= response.status_code < 300:
        return None
    hint = STATUS_HINTS.get(response.status_code, "server error" if response.status_code >= 500 else "refused")
    return Outcome("failed", f"HTTP {response.status_code} ({hint})", _limit_headers(response.headers))


def _limit_headers(headers: httpx.Headers) -> dict[str, str]:
    """The rate-limit headers and Retry-After, printed under the table."""
    wanted = ("ratelimit", "retry-after")
    return {name: value for name, value in headers.items() if any(part in name.lower() for part in wanted)}


def llm_check(provider: Provider) -> Check:
    """One call through the wrapper's provider client, with the provider's base URL, key name and model."""

    def run(env: Mapping[str, str], http: httpx.Client) -> Outcome:
        config = with_env_overrides(load_llm_config(), env)
        settings = config.provider(provider)
        key = _key(env, settings.api_key_env)
        if not key:
            return _not_configured(settings.api_key_env)
        model = next(m.model for m in config.models if m.provider == provider)
        # Plain text, so the reply can be checked for OK; the app's JSON mode is checked by scripts.smoke_llm.
        adapter = OpenAICompatibleAdapter(provider, settings.base_url, key, http)
        note = model
        try:
            reply = adapter.call(model, SYSTEM, PROMPT, MAX_OUTPUT_TOKENS, TIMEOUT_S)
            replied = bool(reply.text.strip())
        except ProviderError:  # a thinking model can answer 5 tokens with no message text at all
            replied = False
        if not replied:  # a real error (bad request, no connection) simply fails again here and is reported
            reply = adapter.call(model, SYSTEM, PROMPT, REASONING_OUTPUT_TOKENS, TIMEOUT_S)
            note += f" (replied only with {REASONING_OUTPUT_TOKENS} output tokens: it reasons first)"
        if "OK" not in reply.text.upper():
            return Outcome("failed", f"{model}: the reply did not contain OK", reply.ratelimit_headers)
        return Outcome("ok", note, reply.ratelimit_headers)

    return run


def serper(env: Mapping[str, str], http: httpx.Client) -> Outcome:
    key = _key(env, "SERPER_API_KEY")
    if not key:
        return _not_configured("SERPER_API_KEY")
    response = http.post(
        "https://google.serper.dev/search",
        json={"q": "laptop", "gl": "in"},
        headers={"X-API-KEY": key},
        timeout=TIMEOUT_S,
    )
    if failure := _http_failure(response):
        return failure
    data = response.json()
    results = len(data.get("organic") or [])
    if not results:
        return Outcome("failed", "answered, but with no search results")
    return Outcome("ok", f"{results} results; credits used: {data.get('credits', 'not reported')}")


def reddit(env: Mapping[str, str], http: httpx.Client) -> Outcome:
    client_id, secret = _key(env, "REDDIT_CLIENT_ID"), _key(env, "REDDIT_CLIENT_SECRET")
    if not (client_id and secret):
        return _not_configured("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET")
    agent = _key(env, "REDDIT_USER_AGENT")
    if not agent or "<" in agent:  # Reddit's rule: <platform>:<app ID>:<version> (by /u/<username>)
        return Outcome("failed", "REDDIT_USER_AGENT is not set in Reddit's required format")
    token = http.post(
        "https://www.reddit.com/api/v1/access_token",
        data={"grant_type": "client_credentials"},
        auth=(client_id, secret),
        headers={"User-Agent": agent},
        timeout=TIMEOUT_S,
    )
    if failure := _http_failure(token):
        return failure
    access = token.json().get("access_token")
    if not access:
        return Outcome("failed", "no access token in Reddit's answer")
    listing = http.get(
        REDDIT_LISTING,
        params={"limit": 1},
        headers={"Authorization": f"bearer {access}", "User-Agent": agent},
        timeout=TIMEOUT_S,
    )
    if failure := _http_failure(listing):
        return failure
    return Outcome("ok", "app-only token, one listing with limit=1", _limit_headers(listing.headers))


def youtube(env: Mapping[str, str], http: httpx.Client) -> Outcome:
    key = _key(env, "YOUTUBE_API_KEY")
    if not key:
        return _not_configured("YOUTUBE_API_KEY")
    response = http.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"part": "id", "id": PUBLIC_VIDEO_ID},
        headers={"X-Goog-Api-Key": key},  # in a header, so the key is never part of a URL that could be logged
        timeout=TIMEOUT_S,
    )
    if failure := _http_failure(response):
        return failure
    if len(response.json().get("items") or []) != 1:
        return Outcome("failed", "answered, but the public test video was not found")
    return Outcome("ok", "videos.list, 1 quota unit")


def jina(env: Mapping[str, str], http: httpx.Client) -> Outcome:
    key = _key(env, "JINA_API_KEY")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    response = http.get(f"https://r.jina.ai/{JINA_TEST_PAGE}", headers=headers, timeout=TIMEOUT_S)
    if failure := _http_failure(response):
        return failure
    if "Example Domain" not in response.text:
        return Outcome("failed", "answered, but not with the test page's text")
    return Outcome("ok", "read example.com " + ("with JINA_API_KEY" if key else "without a key (the lower free limit)"))


def huggingface(env: Mapping[str, str], http: httpx.Client) -> Outcome:
    key = _key(env, "HF_TOKEN")
    if not key:
        return _not_configured("HF_TOKEN")
    response = http.get(
        "https://huggingface.co/api/whoami-v2", headers={"Authorization": f"Bearer {key}"}, timeout=TIMEOUT_S
    )
    if failure := _http_failure(response):
        return failure
    token = (response.json().get("auth") or {}).get("accessToken") or {}
    role = token.get("role", "not reported")
    note = f"whoami; token role: {role}"
    if role == "write":
        note += " (use a read-only or fine-grained read token instead)"
    return Outcome("ok", note)


CHECKS: dict[str, Check] = {
    **{name: llm_check(Provider(name)) for name in LLM_SERVICES},
    "serper": serper,
    "reddit": reddit,
    "youtube": youtube,
    "jina": jina,
    "huggingface": huggingface,
}


def run_checks(services: list[str], env: Mapping[str, str], http: httpx.Client, include: set[str]) -> list[Row]:
    """Every service in order. A crash in one check becomes that service's failed row, never the others' problem."""
    rows = []
    for name in services:
        if name in OPT_IN and name not in include:
            rows.append(Row(name, "skipped", None, OPT_IN[name]))
            continue
        started = monotonic()
        try:
            outcome = CHECKS[name](env, http)
        except ProviderFailure as exc:  # names the provider and the HTTP status only, never the key
            outcome = Outcome("failed", str(exc), exc.ratelimit_headers)
        except httpx.TimeoutException:
            outcome = Outcome("failed", f"no answer within {TIMEOUT_S:g} s")
        except Exception as exc:  # report the type only: a message could quote a URL or a reply
            outcome = Outcome("failed", f"{type(exc).__name__} during the check")
        latency = None if outcome.status == "skipped" else round((monotonic() - started) * 1000)
        rows.append(Row(name, outcome.status, latency, outcome.note, outcome.headers))
    return rows


def render(rows: list[Row]) -> str:
    lines = [f"{'service':<12} {'status':<8} {'latency':>8}  note"]
    for r in rows:
        latency = f"{r.latency_ms} ms" if r.latency_ms is not None else "-"
        lines.append(f"{r.service:<12} {r.status:<8} {latency:>8}  {r.note}")
    for r in rows:
        if r.headers:
            lines.append(f"\n{r.service} rate-limit headers:")
            lines += [f"  {name}: {value}" for name, value in sorted(r.headers.items())]
    return "\n".join(lines)


def redact(text: str, env: Mapping[str, str]) -> str:
    """Belt and braces: mask any configured key value, should one ever reach the output."""
    for name in KEY_NAMES:
        value = _key(env, name)
        if len(value) >= 6:
            text = text.replace(value, "***")
    return text


def _services(raw: str | None, parser: argparse.ArgumentParser) -> list[str]:
    if not raw:
        return list(SERVICES)
    chosen = [part.strip().lower() for part in raw.split(",") if part.strip()]
    unknown = sorted(set(chosen) - set(SERVICES))
    if unknown:
        parser.error(f"unknown service(s): {', '.join(unknown)}; choose from {', '.join(SERVICES)}")
    return [name for name in SERVICES if name in chosen]


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None, http: httpx.Client | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--only", metavar="A,B", help=f"check only these: {', '.join(SERVICES)}")
    parser.add_argument("--include", metavar="A,B", default="", help="opt-in services to run too: serper")
    args = parser.parse_args(argv)
    services = _services(args.only, parser)
    include = {part.strip().lower() for part in args.include.split(",") if part.strip()}
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs request URLs at INFO
    if env is None:
        load_dotenv()  # the root .env, found walking up from here; real environment variables win
        env = os.environ
    with http or httpx.Client(timeout=TIMEOUT_S) as client:
        rows = run_checks(services, env, client, include)
    print(redact(render(rows), env))
    return 1 if any(r.status == "failed" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
