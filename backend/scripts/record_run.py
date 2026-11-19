"""Record what the pipeline really sends and receives, so tests can replay it with no network (US-32).

    python -m scripts.record_run "best laptop for coding under 70k" --name laptop_coding_70k --by "Your Name"

This spends real quota. It is capped, and it refuses rather than risks a leak:
- at most 5 runs per invocation;
- a call budget per service, printed before the first call and enforced on every call. Only Groq has one unless you
  add --budget mistral=2. A call over budget is never sent: it fails like a network error, so the stage's fallback
  answers. A host that is not a known service is refused the same way;
- review, maker and store pages (US-49): a page on a site research reads (config/research.yaml's site lists, through
  research's own page_kind) and that site's robots.txt are the `pages` service, 0 unless you add --budget pages=N.
  A page fetched directly is never written: research's cleaned, capped text of it is, in pipeline.json. (A Jina reader
  reply is written as before, with Reddit links hidden: keep --budget jina at 0 for a gate recording.);
- at most two recording sessions a sprint (US-49): evals/recordings_log.json lists each session, one UTC date, and a
  recording on a third date in the same sprint is refused before any call;
- a stage that times out leaves its thread running, so the recorder stays in place until that thread has ended,
  and refuses anything new once the app has finished, a late retry included;
- a Reddit budget above 0 is refused, and so is a reddit.com, old.reddit.com or redd.it URL, or a Reddit archive's,
  through any service: Reddit data never goes into the repository (D-53 caps Reddit caching at 24 hours). Tests that
  need Reddit use synthetic threads;
- an existing recording is kept unless you add --force;
- a run that failed writes nothing, so a bad key can't replace a good recording: every run must end with code 0 and
  every call must answer 2xx, unless you add --keep-failures;
- REDDIT_SOURCE=official is refused, because PRAW does not go through httpx and so could not be capped or redacted.

It wraps httpx's transport, which every other call in the app goes through, then runs app.cli with --no-db and an
in-memory criteria store, so no database is touched and no saved criteria can hide a call. The Reddit cache is not
read either, because a hit would hide a call and bring in text this script could not recognise, and the research
cache is off (RESEARCH_CACHE=off), so every search and page is really fetched.

Besides the calls, a run keeps what the pipeline made of them (US-49), in pipeline.json: the rubric searched, research's
sources (never a Reddit one), the extraction replies the client accepted (by their prompt's hash), and the result's
products. evals/gate.py replays extraction from it with no network.

Nothing reaches the disk until the run has ended and every call has been cleaned:
- headers: only content-type, x-ratelimit-* and retry-after survive. Query strings keep their names, never values;
- environment: every value of a secret-named variable, and every other value of 8 or more characters, is replaced,
  and the variables' names are printed. If a value still survives, nothing is written. A plain variable holding
  exactly the name given with --by is the one exception. Short values can't be told from ordinary text, so they stay;
- Reddit: no call is ever sent, but search results that point at Reddit carry its title and snippet. Default-deny:
  such a result keeps numbers and ids, its link keeps only its scheme and host, and every other string becomes the
  marker. A plain Reddit link elsewhere, such as in a prompt, keeps only its scheme and host too; an encoded one can
  slip past, so read a recording before committing it. If a Reddit reply ever reached the recorder it would be
  default-denied as well, though a relative permalink would keep its path;
- text from Reddit in other calls: an LLM call declared DataClass.REDDIT, or whose prompt carries Reddit text, has
  its prompt and whole reply replaced, and what that reply said then counts as Reddit text too. A mixed prompt is
  declared private, which is why the text is matched as well. Any other run of 5 or more words matching Reddit text
  is replaced wherever it appears. The shopper's own request never counts as Reddit text.

Tests that need Reddit text use small synthetic threads written for the test.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import re
import shutil
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import httpx
from dotenv import load_dotenv

import app.cli
import app.presearch
from app.contracts import DataClass
from app.criteria import InMemoryCriteriaStore
from app.llm import WrapperLLMClient
from app.research import usage
from app.research.reddit.cache import RedditCache

RECORDINGS = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "recorded"
RECORDINGS_LOG = Path(__file__).resolve().parents[1] / "evals" / "recordings_log.json"
MANIFEST = "manifest.json"
README = "README.md"
PIPELINE = "pipeline.json"
MAX_SESSIONS = 2  # recording sessions a sprint (US-49): each one UTC date
MARKER = "[reddit text not stored]"
LINK_MARKER = "[reddit link not stored]"
REDACTED = "[redacted]"
MAX_RUNS = 5
WAIT_FOR_CALLS_S = 60.0  # longer than any provider's request_timeout_s in config/models.yaml

LLM_SERVICES = ("groq", "gemini", "mistral", "openrouter", "cerebras")
DEFAULT_BUDGET = {
    "groq": 6,
    **dict.fromkeys(LLM_SERVICES[1:], 0),
    "serper": 0,
    "youtube": 0,
    "reddit": 0,
    "jina": 0,
    "pages": 0,  # review, maker and store pages and their robots.txt (US-49)
}
HOSTS = {
    "api.groq.com": "groq",
    "generativelanguage.googleapis.com": "gemini",
    "openrouter.ai": "openrouter",
    "api.cerebras.ai": "cerebras",
    "api.mistral.ai": "mistral",
    "google.serper.dev": "serper",
    "www.googleapis.com": "youtube",
    "r.jina.ai": "jina",
    "arctic-shift.photon-reddit.com": "reddit",
    "api.pullpush.io": "reddit",
    "oauth.reddit.com": "reddit",
    "www.reddit.com": "reddit",
}
REDDIT_DOMAINS = ("reddit.com", "redd.it")
REDDIT_REFUSED = (
    "Reddit can't have a budget. Reddit data never goes into the repository (D-53 caps Reddit caching at 24 hours). "
    "Tests that need Reddit use synthetic threads."
)
REDDIT_SOURCE_HINT = "Reddit is never called while recording, so set REDDIT_SOURCE=off for this run."
REFUSED_ROW_NOTE = (
    "With --keep-failures, a refused call is written as a row with no reply: just the service, the URL with no query "
    "values or Reddit path, and the error."
)

KEPT_HEADERS = ("content-type", "retry-after")
SECRET_NAME_PARTS = {"KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "COOKIE", "CREDENTIALS", "SALT", "URL", "DSN"}
MIN_SECRET_LENGTH = 4  # a secret-named variable set to "1" or "on" holds no secret
MIN_VALUE_LENGTH = 8

REDDIT_ID_KEYS = {"id", "link_id", "parent_id"}
REDDIT_URL_KEYS = {"permalink", "url", "link"}
REDDIT_TIME_KEYS = {"created", "created_utc", "retrieved_on", "retrieved_utc", "utc_datetime_str"}
REDDIT_USERNAME_KEYS = {"author", "author_fullname"}
REDDIT_ID = re.compile(r"(t\d_)?[a-z0-9]{1,16}")
REDDIT_FULLNAME = re.compile(r"t\d_[a-z0-9]{1,16}")
REDDIT_TIME = re.compile(r"[\d :.TZ+-]{4,32}")
REDDIT_SLUG = re.compile(r"(/comments/[a-z0-9]+)/[^/?#]+")
REDDIT_USER_PATH = re.compile(r"/(u|user)/|/r/u_", re.IGNORECASE)
REDDIT_HOST = r"(?:[\w-]+\.)*(?:reddit\.com|redd\.it)"
# A Reddit host inside a URL, or a whole value that is one. The reader takes r.jina.ai/reddit.com/... and ?url=...
REDDIT_TARGET = re.compile(rf"(?:^(?=\S*$)|[/=@,;\\]){REDDIT_HOST}(?![\w-]|\.\w)", re.IGNORECASE)
# A link to a Reddit page in running text. The lookahead leaves a link that was already cleaned alone.
REDDIT_LINK = re.compile(
    rf"(?<![\w./-])(?:https?://)?(?:[\w.:%+-]+@)?{REDDIT_HOST}\.?(?::\d+)?(?!/\[reddit)/[^\s\"'<>\\]+", re.IGNORECASE
)
LLM_KEPT_KEYS = {"id", "object", "model", "role", "finish_reason"}
URL = re.compile(r"https?://\S+")
QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')
WORD = re.compile(r"[\w']+")  # any script: a quote in Hindi is still a quote
RUN_WORDS = 5  # a run this long is Reddit's text, not a coincidence
MIN_RUN_WORDS = 3  # a shorter whole text counts as a run of its own length, down to this

Send = Callable[[httpx.Request], httpx.Response]
Runner = Callable[[str], int | None]

DECLARED_CLASS: ContextVar[DataClass | None] = ContextVar("declared_class", default=None)


# What the app's own caps (app/research/usage.py) count for a call to each service. A call the recorder refuses was
# never sent, so its units go back, as the live run budget gives them back (D-78); one it sends stays counted.
APP_UNITS: dict[str, tuple[str, ...]] = {
    "serper": ("serper",),
    "jina": ("jina",),
    "pages": ("pages",),
    "youtube": ("youtube_search", "youtube"),
    "reddit": ("reddit_archive",),
}
APP_UNITS_BY_HOST: dict[str, tuple[str, ...]] = {"api.firecrawl.dev": ("firecrawl", "firecrawl_pages")}


def app_units(request: httpx.Request, service: str | None) -> tuple[str, ...]:
    """What the app counted for this request: by its host first (Firecrawl, even reading a Reddit page), then a
    robots.txt (research's pages unit, Reddit's own robots file too), then the service."""
    host = request.url.host
    if host in APP_UNITS_BY_HOST:
        return APP_UNITS_BY_HOST[host]
    if request.url.path == "/robots.txt":
        return ("pages",)
    return APP_UNITS.get(service or "", ())


def sent(providers: tuple[str, ...]) -> None:
    for provider in providers:
        usage.sent(provider)


def not_sent(providers: tuple[str, ...]) -> None:
    for provider in providers:
        usage.not_sent(provider)


class Refused(httpx.TransportError):
    """A call the recorder would not send. The app sees a network error and falls back, exactly as in a real outage."""


@dataclass
class Call:
    """One call as it happened. Held in memory only: it still has everything in it until clean() has run."""

    run: int
    service: str
    method: str
    url: httpx.URL
    request_headers: Mapping[str, str]
    request_content: bytes
    data_class: DataClass | None = None
    targets_reddit: bool = False
    over_budget: bool = False
    status: int | None = None
    response_headers: Mapping[str, str] = field(default_factory=dict)
    response_content: bytes = b""
    elapsed_ms: int = 0
    error: str | None = None


class Recorder:
    def __init__(self, budget: Mapping[str, int], send: Send | None) -> None:
        self.budget = budget
        self.send = send
        self.used = dict.fromkeys(budget, 0)
        self.calls: list[Call] = []
        self.refused: dict[str, int] = {}
        self.run = 0
        self.in_flight = 0
        self.closed = False
        self.lock = threading.Lock()
        self.threads_before: set[threading.Thread] = set()

    def refuse(self, reason: str) -> Refused:
        """Called with the lock held."""
        if reason not in self.refused:
            print(f"Stopped a call: {reason}")
        self.refused[reason] = self.refused.get(reason, 0) + 1
        return Refused(reason)

    def why_refused(self, call: Call) -> str | None:
        """Called with the lock held. None means the call may go out."""
        if self.closed:
            return "the app had finished, so a late call was not sent"
        if call.targets_reddit:
            return REDDIT_REFUSED
        limit = self.budget[call.service]
        if self.used[call.service] < limit:
            return None
        if call.service == "reddit":
            return REDDIT_REFUSED
        call.over_budget = True
        return f"the {call.service} budget of {limit} is spent"

    def admit(self, request: httpx.Request) -> Call:
        """Check and count under one lock, so two threads can't both take the last call of a budget."""
        host = request.url.host
        service = HOSTS.get(host) or ("pages" if is_research_page(str(request.url)) else None)
        targets_reddit = fetches_reddit(request, service)
        if targets_reddit and service is None:
            service = "reddit"  # old.reddit.com and redd.it: refused for what they are, not as unknown hosts
        with self.lock:
            if service is None:
                not_sent(app_units(request, None))
                raise self.refuse(f"{host} is not a known service")
            call = Call(
                self.run,
                service,
                request.method,
                request.url,
                request.headers,
                request.read(),
                DECLARED_CLASS.get(),
                targets_reddit,
            )
            self.calls.append(call)
            reason = self.why_refused(call)
            if reason is not None:
                call.error = Refused.__name__
                not_sent(app_units(request, service))  # never sent, so the app's own caps don't count it (D-78)
                raise self.refuse(reason)
            sent(app_units(request, service))
            self.used[service] += 1
            self.in_flight += 1
            return call

    def handle(self, request: httpx.Request, send: Send) -> httpx.Response:
        call = self.admit(request)
        started = time.perf_counter()
        try:
            response = send(request)
            content = response.read()
            call.status, call.response_headers, call.response_content = response.status_code, response.headers, content
        except Exception as error:
            call.error = type(error).__name__
            raise
        finally:
            call.elapsed_ms = round((time.perf_counter() - started) * 1000)
            with self.lock:
                self.in_flight -= 1
        # read() has decoded the body, so the app gets it without the headers that describe the encoded form.
        headers = [
            (name, value)
            for name, value in response.headers.multi_items()
            if name.lower() not in ("content-encoding", "content-length", "transfer-encoding")
        ]
        return httpx.Response(response.status_code, headers=headers, content=content, request=request)

    def finish(self) -> bool:
        """Refuse anything new, then wait for the threads the app left running. True once they have all ended.

        Waiting for calls alone is not enough: a thread whose reply has just arrived may be about to retry.
        """
        with self.lock:
            self.closed = True
        deadline = time.monotonic() + WAIT_FOR_CALLS_S
        while self.in_flight or set(threading.enumerate()) - self.threads_before:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)
        return True

    def refused_so_far(self) -> dict[str, int]:
        with self.lock:
            return dict(self.refused)

    def problems(self) -> list[str]:
        failed = [f"{c.service} call failed: {c.error}" for c in self.calls if c.error and c.error != Refused.__name__]
        answered = [
            f"{c.service} call answered {c.status}" for c in self.calls if c.status and not 200 <= c.status < 300
        ]
        return failed + answered + [f"refused {count}: {reason}" for reason, count in self.refused_so_far().items()]

    @contextmanager
    def wrapping_the_app(self) -> Iterator[None]:
        real_send = httpx.HTTPTransport.handle_request
        real_complete = WrapperLLMClient.complete
        real_cache_get = RedditCache.get
        recorder = self
        self.threads_before = set(threading.enumerate())

        def handle_request(transport: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
            return recorder.handle(request, recorder.send or (lambda r: real_send(transport, r)))

        def complete(client: WrapperLLMClient, *args: Any, data_class: DataClass, **kwargs: Any) -> Any:
            token = DECLARED_CLASS.set(data_class)  # the call goes out on this thread
            try:
                answer = real_complete(client, *args, data_class=data_class, **kwargs)
            finally:
                DECLARED_CLASS.reset(token)
            keep_accepted(args, kwargs, data_class, answer)
            return answer

        httpx.HTTPTransport.handle_request = handle_request  # type: ignore[method-assign, assignment]
        WrapperLLMClient.complete = complete  # type: ignore[method-assign, assignment]
        RedditCache.get = lambda cache, key: None  # type: ignore[method-assign, assignment]
        try:
            yield
        finally:
            # If a thread never ends, stay wrapped, so its next call still meets the recorder. The process ends soon.
            if self.finish():
                httpx.HTTPTransport.handle_request = real_send  # type: ignore[method-assign]
                WrapperLLMClient.complete = real_complete  # type: ignore[method-assign]
                RedditCache.get = real_cache_get  # type: ignore[method-assign]


def is_research_page(url: str) -> bool:
    """A page on a site research reads, or that site's robots.txt: research's own page_kind decides, never a list here.
    Reddit, YouTube, social sites and logged-in pages are never one."""
    from app.research.pages import page_kind  # here: the research package is only needed while recording

    return page_kind(url, research_sites()) is not None


@functools.cache
def research_sites() -> Any:
    from app.research.settings import load_research_settings

    return load_research_settings()


# --- headers, URLs and the environment -------------------------------------------------------------


def kept_headers(headers: Mapping[str, str]) -> dict[str, str]:
    kept = {name.lower(): value for name, value in headers.items()}
    return {n: v for n, v in sorted(kept.items()) if n in KEPT_HEADERS or n.startswith("x-ratelimit-")}


def redacted_url(url: httpx.URL) -> str:
    port = f":{url.port}" if url.port else ""
    bare = f"{url.scheme}://{url.host}{port}{url.path}"
    names = [name for name, _ in url.params.multi_items()]
    return f"{bare}?{'&'.join(f'{name}={REDACTED}' for name in names)}" if names else bare


def secret_named(name: str) -> bool:
    return bool(SECRET_NAME_PARTS & set(name.upper().split("_")))


def must_hide(name: str, value: str) -> bool:
    value = value.strip()
    return len(value) >= (MIN_SECRET_LENGTH if secret_named(name) else MIN_VALUE_LENGTH)


def secrets_in(env: Mapping[str, str]) -> dict[str, str]:
    return {name: value.strip() for name, value in env.items() if must_hide(name, value)}


def forms_of(value: str) -> list[str]:
    """The value as it is, inside a JSON string, and in a URL."""
    return list({value, json.dumps(value, ensure_ascii=False)[1:-1], quote(value, safe="")})


def hide_values(value: Any, secrets: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        for secret in sorted(secrets.values(), key=len, reverse=True):  # longest first: one may contain another
            for form in forms_of(secret):
                value = value.replace(form, REDACTED)
        return value
    if isinstance(value, list):
        return [hide_values(item, secrets) for item in value]
    if isinstance(value, dict):
        return {hide_values(key, secrets): hide_values(item, secrets) for key, item in value.items()}
    return value


def find_leaks(text: str, secrets: Mapping[str, str]) -> list[str]:
    """The names, never the values, of the variables whose value is in the text."""
    return sorted(name for name, value in secrets.items() if any(form in text for form in forms_of(value)))


def count_values(value: Any, secrets: Mapping[str, str]) -> dict[str, int]:
    """How often each variable's value appears, by name."""
    text = to_json(value)
    counts = {name: sum(text.count(form) for form in forms_of(secret)) for name, secret in secrets.items()}
    return {name: count for name, count in sorted(counts.items()) if count}


# --- Reddit ----------------------------------------------------------------------------------------


def runs_of(words: list[str], size: int) -> set[tuple[str, ...]]:
    return {tuple(words[i : i + size]) for i in range(len(words) - size + 1)}


def words_of(text: str) -> list[str]:
    return [word.lower() for word in WORD.findall(text)]


class RedditMemory:
    """Every piece of Reddit text seen in this recording, so it can be recognised wherever else it turns up."""

    def __init__(self, shopper_text: str = "") -> None:
        self.runs: set[tuple[str, ...]] = set()
        self.username_patterns: set[str] = set()
        words = words_of(shopper_text)
        self.shoppers_own = {run for size in range(MIN_RUN_WORDS, RUN_WORDS + 1) for run in runs_of(words, size)}

    def remember(self, text: str) -> None:
        words = words_of(URL.sub(" ", text))  # the words of a URL would match the URLs that are kept on purpose
        size = min(len(words), RUN_WORDS)
        if size >= MIN_RUN_WORDS:
            self.runs |= runs_of(words, size) - self.shoppers_own

    def remember_username(self, name: str) -> None:
        """A plain-word name, such as "Laptop", can't be told from the word, so only u/name gives it away."""
        escaped = re.escape(name)
        self.username_patterns.add(rf"\bu/{escaped}\b" if name.isalpha() else rf"(?<![\w-]){escaped}(?![\w-])")

    def username_spans(self, text: str) -> list[tuple[int, int]]:
        return [m.span() for p in self.username_patterns for m in re.finditer(p, text, re.IGNORECASE)]

    def spans(self, text: str) -> list[tuple[int, int]]:
        """Where Reddit text sits in this text, as character ranges."""
        found = list(WORD.finditer(text))
        words = [m.group().lower() for m in found]
        spans = self.username_spans(text)
        for size in range(MIN_RUN_WORDS, RUN_WORDS + 1):
            for i in range(len(words) - size + 1):
                if tuple(words[i : i + size]) in self.runs:
                    spans.append((found[i].start(), found[i + size - 1].end()))
        return spans

    def replace(self, text: str) -> str:
        return put_marker(text, self.spans(text))

    def replace_usernames(self, text: str) -> str:
        return put_marker(text, self.username_spans(text))


def put_marker(text: str, spans: list[tuple[int, int]]) -> str:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    for start, end in reversed(merged):
        text = text[:start] + MARKER + text[end:]
    return text


def decoded(text: str) -> str:
    """A URL inside a URL is often encoded, and sometimes more than once."""
    plain = unquote(text)
    while plain != text:
        text, plain = plain, unquote(plain)
    return plain


def names_reddit(text: str, service: str | None) -> bool:
    """Is this a Reddit address, or does it hold one? A Reddit source's own host counts only inside another service."""
    plain = decoded(text)
    if REDDIT_TARGET.search(plain):
        return True
    archives = (host for host, name in HOSTS.items() if name == "reddit" and not points_at_reddit(host))
    return service != "reddit" and any(host in plain.lower() for host in archives)


def fetches_reddit(request: httpx.Request, service: str | None) -> bool:
    """A prompt or a search query may mention Reddit; only fetching it is refused. An LLM fetches nothing."""
    if names_reddit(str(request.url), service):
        return True
    if service in LLM_SERVICES:
        return False
    return any(names_reddit(text, service) for text in free_text(parsed(request.read())))


def hidden_link(url: str) -> str:
    """Only the scheme and host: a Reddit link's path names the subreddit and the thread."""
    parts = urlsplit(url if url.lower().startswith(("http://", "https://")) else f"https://{url}")
    return f"{parts.scheme}://{(parts.hostname or '').rstrip('.')}/{LINK_MARKER}"


def hide_reddit_links(text: str) -> str:
    return REDDIT_LINK.sub(lambda link: hidden_link(link.group()), text)


def clean_reddit_url(url: str) -> str | None:
    """A link to Reddit keeps its scheme and host. Any other loses its title slug, query and fragment.

    None if it isn't a URL, or if it names a user.
    """
    if points_at_reddit(url):
        return hidden_link(url)
    if not url.startswith(("/r/", "/comments/", "https://", "http://")) or REDDIT_USER_PATH.search(url):
        return None
    bare = re.split(r"[?#]", url, maxsplit=1)[0]
    return REDDIT_SLUG.sub(r"\1", bare).rstrip("/")


def clean_reddit_text(value: Any, memory: RedditMemory, key: str = "") -> Any:
    """Default-deny: a string survives only under a key we know holds an id, a time or a URL, and only in that shape."""
    if isinstance(value, dict):
        return {name: clean_reddit_text(item, memory, name) for name, item in value.items()}
    if isinstance(value, list):
        return [clean_reddit_text(item, memory, key) for item in value]
    if not isinstance(value, str):
        return value
    if key in REDDIT_ID_KEYS and REDDIT_ID.fullmatch(value):
        return value
    if key == "name" and REDDIT_FULLNAME.fullmatch(value):
        return value
    if key in REDDIT_TIME_KEYS and REDDIT_TIME.fullmatch(value):
        return value
    if key in REDDIT_URL_KEYS:
        url = clean_reddit_url(value)
        if url is not None:
            return url
    if key in REDDIT_USERNAME_KEYS:
        memory.remember_username(value)
    memory.remember(value)
    return MARKER


def points_at_reddit(value: str) -> bool:
    """Is this whole value a link to Reddit, with or without its scheme?"""
    if any(character.isspace() for character in value):
        return False
    try:
        host = urlsplit(value if value.lower().startswith(("http://", "https://")) else f"//{value}").hostname or ""
    except ValueError:
        return False  # not a URL at all, such as a title with a bracket in it
    host = host.lower().rstrip(".")
    return any(host == domain or host.endswith(f".{domain}") for domain in REDDIT_DOMAINS)


def clean_reddit_results(value: Any, memory: RedditMemory) -> Any:
    """A search result that links to Reddit carries Reddit's title and snippet, so it is treated as a Reddit item."""
    if isinstance(value, dict):
        if any(isinstance(item, str) and points_at_reddit(item) for item in value.values()):
            return clean_reddit_text(value, memory)
        return {key: clean_reddit_results(item, memory) for key, item in value.items()}
    if isinstance(value, list):
        return [clean_reddit_results(item, memory) for item in value]
    return value


def free_text(value: Any, key: str = "") -> Iterator[str]:
    """Each piece of free text in an LLM body, leaf by leaf, looking inside a reply that is itself JSON."""
    if isinstance(value, dict):
        for name, item in value.items():
            yield from free_text(item, name)
    elif isinstance(value, list):
        for item in value:
            yield from free_text(item, key)
    elif isinstance(value, str) and key not in LLM_KEPT_KEYS:
        inner = parsed(value.encode("utf-8"))
        if isinstance(inner, dict | list):
            yield from free_text(inner)
        else:
            yield value
            yield from QUOTED.findall(value)  # fenced or wrapped JSON doesn't parse, but its strings are still there


def mark_strings(value: Any, key: str = "") -> Any:
    """Replace every string except the ones naming the model and the shape of the reply."""
    if isinstance(value, dict):
        return {name: mark_strings(item, name) for name, item in value.items()}
    if isinstance(value, list):
        return [mark_strings(item, key) for item in value]
    if isinstance(value, str) and key not in LLM_KEPT_KEYS:
        return MARKER
    return value


def map_strings(value: Any, change: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return change(value)
    if isinstance(value, list):
        return [map_strings(item, change) for item in value]
    if isinstance(value, dict):
        return {key: map_strings(item, change) for key, item in value.items()}
    return value


# --- from calls to files ---------------------------------------------------------------------------


def parsed(content: bytes) -> Any:
    text = content.decode("utf-8", "replace")
    try:
        return json.loads(text)
    except ValueError:
        return text


def page_body_note(content: bytes) -> str:
    return f"[page body not stored: {len(content)} bytes]"


def clean(calls: list[Call], shopper_text: str, memory: RedditMemory | None = None) -> tuple[list[dict[str, Any]], int]:
    """The calls as they may be written, with Reddit text gone, and how many LLM calls were replaced whole.

    Secrets are handled last, on the finished files. Pass `memory` to keep what it learnt about Reddit text, so the
    pipeline's record can be cleaned of it too.
    """
    memory = memory if memory is not None else RedditMemory(shopper_text)
    rows: list[dict[str, Any]] = []
    from_reddit: list[bool] = []
    for call in calls:
        request_body = parsed(call.request_content) if call.request_content else None
        response_body = parsed(call.response_content) if call.response_content else None
        url = redacted_url(call.url)
        is_reddit = call.service == "reddit" or call.targets_reddit
        if is_reddit:
            request_body = clean_reddit_text(request_body, memory)
            response_body = clean_reddit_text(response_body, memory)
            url = hidden_link(url) if call.targets_reddit else REDDIT_SLUG.sub(r"\1", url)
        elif call.service == "pages":  # a site's own page: its cleaned text is in pipeline.json, never its HTML
            response_body = page_body_note(call.response_content) if call.response_content else None
        elif call.service not in LLM_SERVICES:
            response_body = clean_reddit_results(response_body, memory)
        row = {
            "run": call.run,
            "service": call.service,
            "method": call.method,
            "url": url,
            "request_headers": kept_headers(call.request_headers),
            "request_body": request_body,
            "status": call.status,
            "response_headers": kept_headers(call.response_headers),
            "response_body": response_body,
            "elapsed_ms": call.elapsed_ms,
        }
        rows.append({**row, "error": call.error} if call.error else row)
        from_reddit.append(is_reddit)

    marked = 0
    for call, row in zip(calls, rows, strict=True):  # in call order: an earlier reply can only reach a later prompt
        if call.service not in LLM_SERVICES:
            continue
        declared = call.data_class is DataClass.REDDIT
        if declared or any(memory.spans(text) for text in free_text(row["request_body"])):
            for text in free_text(row["response_body"]):
                memory.remember(text)
            row["request_body"] = mark_strings(row["request_body"])
            row["response_body"] = mark_strings(row["response_body"])
            marked += 1
    for row, is_reddit in zip(rows, from_reddit, strict=True):
        # What a Reddit row keeps, it keeps on purpose. Everywhere else, the words and the links both go.
        change = memory.replace_usernames if is_reddit else (lambda text: hide_reddit_links(memory.replace(text)))
        row["request_body"] = map_strings(row["request_body"], change)
        row["response_body"] = map_strings(row["response_body"], change)
    return rows, marked


def clean_pipeline(
    runs: list[dict[str, Any]], memory: RedditMemory, secrets: Mapping[str, str] | None = None
) -> list[dict[str, Any]]:
    """The pipeline's record as it may be written: no Reddit source at all, and any Reddit text seen in a call gone
    from every other string, as in the calls themselves.

    A source whose text or title the cleaning would change is left out whole, never rewritten: the gate rebuilds each
    prompt from the source and finds the accepted reply by the prompt's hash, so a changed page would silently lose its
    reply, and a longer one could pass the source's own size limits. The same goes for a page holding a secret."""

    def clean(text: str) -> str:
        return hide_reddit_links(memory.replace(text))

    def changed(source: Mapping[str, Any]) -> bool:
        texts = [str(source.get("text", "")), str(source.get("title", ""))]
        return any(clean(t) != t or hide_values(t, secrets or {}) != t for t in texts)

    kept: list[dict[str, Any]] = []
    for run in runs:
        public = [s for s in run["sources"] if not is_reddit_source(s)]
        sources = [s for s in public if not changed(s)]
        kept.append(
            {
                **run,
                "sources": sources,
                "reddit_sources_left_out": len(run["sources"]) - len(public),
                "changed_sources_left_out": len(public) - len(sources),
            }
        )
    cleaned: list[dict[str, Any]] = map_strings(kept, clean)
    return cleaned


def is_reddit_source(source: Mapping[str, Any]) -> bool:
    """A Reddit thread, Reddit-class text (YouTube with viewers' comments), or anything at a Reddit address."""
    return (
        source.get("type") == "reddit"
        or source.get("data_class") == "reddit"
        or points_at_reddit(str(source.get("url", "")))
        or names_reddit(str(source.get("url", "")), None)
    )


HAND_WRITTEN = ("expected_products", "expected_note")  # a recording's metadata written by hand, never recorded


def kept_by_hand(args: argparse.Namespace) -> dict[str, Any]:
    """What the recording being replaced has written by hand, kept through a --force re-recording."""
    path = getattr(args, "root", RECORDINGS) / args.name / MANIFEST
    if not path.is_file():
        return {}
    old = json.loads(path.read_text(encoding="utf-8"))
    return {key: old[key] for key in HAND_WRITTEN if key in old}


def readme_line(name: str, manifest: Mapping[str, Any]) -> str:
    counts = ", ".join(f"{service}: {count}" for service, count in sorted(manifest["calls"].items()))
    runs = "1 run" if manifest["runs"] == 1 else f"{manifest['runs']} runs"
    when = manifest["recorded_at"][:10]
    by = manifest["recorded_by"]
    note = " Some calls failed or were refused: see manifest.json." if manifest["problems"] else ""
    return (
        f'- `{name}`: real replies to "{manifest["request"]}" ({runs}; calls {counts}). Recorded {when} by {by}.{note}'
    )


def index_text(root: Path, name: str, line: str) -> str:
    path = root / README
    header = (
        "# Recorded responses\n\n"
        "Real replies from outside services, written by `python -m scripts.record_run` and replayed with\n"
        "`tests/fakes/recorded.py`. One line each: what it is, when it was recorded and by whom.\n"
        "See `docs/setup.md`.\n\n"
    )
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    kept = [item for item in lines if item.startswith("- `") and not item.startswith(f"- `{name}`")]
    return header + "\n".join(sorted([*kept, line])) + "\n"


def to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def build_files(
    args: argparse.Namespace,
    recorder: Recorder,
    rows: list[dict[str, Any]],
    problems: list[str],
    secrets: Mapping[str, str],
    pipeline: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, str], str]:
    """The folder's files by name, and the README line for the index."""
    manifest = {
        "name": args.name,
        "request": args.request,
        "runs": args.runs,
        "budget": dict(recorder.budget),
        "calls": {service: count for service, count in recorder.used.items() if count},
        "refused": recorder.refused_so_far(),
        "problems": problems,
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "recorded_by": args.by,
    }
    manifest = {**manifest, **kept_by_hand(args)}  # a re-recording keeps what was written by hand (US-49)
    manifest = hide_values(manifest, secrets)
    line = readme_line(args.name, manifest)
    files = {MANIFEST: to_json(manifest), README: line + "\n"}
    for service in sorted({row["service"] for row in rows}):
        files[f"{service}.json"] = to_json(hide_values([row for row in rows if row["service"] == service], secrets))
    if pipeline:
        files[PIPELINE] = to_json(hide_values(pipeline, secrets))
    return files, line


def write_files(root: Path, name: str, files: Mapping[str, str], index: str) -> None:
    """Write aside, then swap: a failure at any step leaves any old recording whole."""
    root.mkdir(parents=True, exist_ok=True)
    folder, staging, old = root / name, root / f".{name}.writing", root / f".{name}.old"
    for leftover in (staging, old):
        if leftover.exists():
            shutil.rmtree(leftover)
    staging.mkdir()
    try:
        for file_name, text in files.items():
            (staging / file_name).write_text(text, encoding="utf-8", newline="\n")
        if folder.exists():
            folder.rename(old)
        staging.rename(folder)
    except BaseException:
        if old.exists() and not folder.exists():
            old.rename(folder)
        shutil.rmtree(staging, ignore_errors=True)
        raise
    shutil.rmtree(old, ignore_errors=True)
    (root / README).write_text(index, encoding="utf-8", newline="\n")


# --- running ---------------------------------------------------------------------------------------


PIPELINE_RUNS: list[dict[str, Any]] = []  # what each run of run_app made, for pipeline.json (US-49)
ACCEPTED: list[dict[str, Any]] = []  # the extraction replies the client accepted in this run, for pipeline.json
ACCEPTED_LOCK = threading.Lock()


def prompt_hashes(user: str) -> dict[str, str]:
    """A prompt's fingerprints: of the whole, and of its data block (what follows the criteria lines)."""
    import hashlib

    block = user.split("\n\n", 1)[-1]  # what follows the blank line after the criteria
    return {
        "prompt_sha256": hashlib.sha256(user.encode("utf-8")).hexdigest(),
        "data_sha256": hashlib.sha256(block.encode("utf-8")).hexdigest(),
    }


def keep_accepted(args: tuple[Any, ...], kwargs: Mapping[str, Any], data_class: DataClass, answer: Any) -> None:
    """The reply the client accepted for an extraction prompt, as the model it parsed into (US-49): the gate replays
    exactly what the live run used, whichever provider or retry gave it. Never one from Reddit-class text."""
    from app.contracts import LLMTask

    task = args[0] if args else kwargs.get("task")
    user = args[2] if len(args) > 2 else kwargs.get("user")
    if task is not LLMTask.EXTRACT_CLAIMS or data_class is DataClass.REDDIT or not isinstance(user, str):
        return
    dump = getattr(answer, "model_dump", None)
    if dump is None:
        return
    with ACCEPTED_LOCK:
        ACCEPTED.append({"task": task.value, **prompt_hashes(user), "reply": dump(mode="json")})


class _KeepsResearch:
    """Wraps the research stage to keep its sources. Anything else the runner asks goes to the stage itself."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.name = inner.name
        self.is_placeholder = bool(getattr(inner, "is_placeholder", False))
        self.sources: list[Any] = []

    def run(self, inp: Any, ctx: Any) -> Any:
        out = self.inner.run(inp, ctx)
        self.sources = list(out.sources)
        return out

    def fallback(self, inp: Any, ctx: Any, error: Exception) -> Any:
        out = self.inner.fallback(inp, ctx, error)
        self.sources = list(out.sources)
        return out

    def __getattr__(self, name: str) -> Any:
        if name == "inner":
            raise AttributeError(name)
        return getattr(self.inner, name)


def pipeline_record(request: Any, sources: list[Any], result: Any) -> dict[str, Any]:
    """One run's record: the rubric searched, research's sources, and the result's products by group."""
    names = {p.product.id: p.product.name for p in result.products}
    decision = result.decision
    filtered = list(decision.filtered) if decision is not None else []
    return {
        "rubric": request.rubric.model_dump(mode="json"),
        "sources": [source.model_dump(mode="json") for source in sources],
        "shown": [names.get(r.product_id, r.product_id) for r in decision.ranked] if decision is not None else [],
        "filtered_out": [names.get(f.product_id, f.product_id) for f in filtered if f.group == "filtered_out"],
        "cant_tell": [names.get(f.product_id, f.product_id) for f in filtered if f.group == "cant_tell"],
        "citations": len(result.citations),
        "llm_replies": list(ACCEPTED),
    }


def run_app(query: str) -> int:
    """One run of the real CLI. A fresh store each time, so criteria saved earlier can't hide a call. The research
    cache is off, so every search and page is really fetched, and what the pipeline made is kept for pipeline.json."""
    store = InMemoryCriteriaStore()
    saved = app.presearch.default_criteria_store
    cli: Any = app.cli  # its names are swapped for this run only, then put back
    real_stages, real_runner = cli.default_search_stages, cli.PipelineRunner
    kept: dict[str, Any] = {}

    with ACCEPTED_LOCK:
        ACCEPTED.clear()

    def stages() -> Any:
        built = real_stages()
        built.research = kept.setdefault("research", _KeepsResearch(built.research))
        return built

    class KeepingRunner(real_runner):  # type: ignore[misc, valid-type]
        def run(self, request: Any, *args: Any, **kwargs: Any) -> Any:
            result = super().run(request, *args, **kwargs)
            research = kept.get("research")
            PIPELINE_RUNS.append(pipeline_record(request, research.sources if research else [], result))
            return result

    app.presearch.default_criteria_store = lambda: store  # type: ignore[assignment]
    cli.default_search_stages = stages
    cli.PipelineRunner = KeepingRunner
    cache = os.environ.get("RESEARCH_CACHE")
    os.environ["RESEARCH_CACHE"] = "off"
    try:
        return app.cli.main([query, "--no-db"])
    finally:
        app.presearch.default_criteria_store = saved
        cli.default_search_stages = real_stages
        cli.PipelineRunner = real_runner
        if cache is None:
            os.environ.pop("RESEARCH_CACHE", None)
        else:
            os.environ["RESEARCH_CACHE"] = cache


# --- the recordings log (US-49) --------------------------------------------------------------------------------------


def read_log(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"current_sprint": 1, "sessions": []}
    log: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return log


def session_refusal(log: Mapping[str, Any], sprint: int, today: str) -> str | None:
    """Why a recording today would be refused, or None. A session is one UTC date; a sprint has at most two."""
    dates = {s["date"] for s in log.get("sessions", []) if s.get("sprint") == sprint}
    if today in dates or len(dates) < MAX_SESSIONS:
        return None
    return (
        f"Sprint {sprint} already has {MAX_SESSIONS} recording sessions ({', '.join(sorted(dates))}), the most US-49 "
        "allows, so nothing was called."
    )


def logged(log: Mapping[str, Any], sprint: int, today: str, by: str, name: str) -> dict[str, Any]:
    """The log with this recording added to today's session."""
    sessions = [dict(s) for s in log.get("sessions", [])]
    session = next((s for s in sessions if s.get("sprint") == sprint and s["date"] == today), None)
    if session is None:
        session = {"sprint": sprint, "date": today, "by": by, "recordings": []}
        sessions.append(session)
    session["recordings"] = sorted({*session["recordings"], name})
    return {**log, "sessions": sessions}


def print_calls(recorder: Recorder) -> None:
    """What this attempt cost, whether or not it is written: the quota is gone either way."""
    sent = ", ".join(f"{service} {count}" for service, count in recorder.used.items() if count)
    print(f"Calls sent: {sent or 'none'}")
    for call in list(recorder.calls):
        print(f"  {call.service} {call.status or call.error or 'still running'} in {call.elapsed_ms} ms")


def slug(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", value):
        raise argparse.ArgumentTypeError("use lower-case letters, digits, _ and - only")
    return value


def run_count(value: str) -> int:
    runs = int(value)
    if not 1 <= runs <= MAX_RUNS:
        raise argparse.ArgumentTypeError(f"at most {MAX_RUNS} runs per invocation, and at least 1")
    return runs


def budget_entry(value: str) -> tuple[str, int]:
    service, _, count = value.partition("=")
    if service not in DEFAULT_BUDGET or not count.isdigit():
        raise argparse.ArgumentTypeError(f"use service=number, with one of: {', '.join(DEFAULT_BUDGET)}")
    if service == "reddit" and int(count) > 0:
        raise argparse.ArgumentTypeError(REDDIT_REFUSED)
    return service, int(count)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m scripts.record_run", description="Record a capped, redacted run.")
    parser.add_argument("request", help="a made-up shopping request, never a real person's")
    parser.add_argument("--name", required=True, type=slug, help="the folder under tests/fixtures/recorded/")
    parser.add_argument("--by", required=True, help="who is recording, for the README line")
    parser.add_argument("--runs", type=run_count, default=1, help=f"runs of the pipeline, at most {MAX_RUNS}")
    parser.add_argument(
        "--budget",
        action="append",
        type=budget_entry,
        default=[],
        metavar="SERVICE=N",
        help="change one service's budget",
    )
    parser.add_argument("--force", action="store_true", help="replace an existing recording")
    parser.add_argument("--keep-failures", action="store_true", help="write the recording even if a call or run failed")
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    send: Send | None = None,
    runner: Runner = run_app,
    root: Path = RECORDINGS,
    log_path: Path | None = None,
) -> int:
    args = parse_args(argv)
    args.root = root  # where an earlier recording's hand-written metadata is kept
    if log_path is None and root == RECORDINGS:
        log_path = RECORDINGS_LOG  # the real recordings keep the real log; a test's own folder keeps none unless given
    if env is None:
        load_dotenv()  # the root .env, found walking up from here; real environment variables win
        env = os.environ
    if (root / args.name).exists() and not args.force:
        print(f"Recording {args.name} already exists, so nothing was called. Add --force to replace it.")
        return 1
    if env.get("REDDIT_SOURCE", "").strip().lower() == "official":
        print("REDDIT_SOURCE=official can't be recorded: PRAW doesn't go through httpx. Set REDDIT_SOURCE=off.")
        return 1
    today = datetime.now(UTC).date().isoformat()
    log = read_log(log_path) if log_path is not None else None
    sprint = int(log["current_sprint"]) if log is not None else 0  # set in the log at each sprint's start
    if log is not None:
        refusal = session_refusal(log, sprint, today)
        if refusal is not None:
            print(refusal)
            return 1

    budget = {**DEFAULT_BUDGET, **dict(args.budget)}
    print("Call budget (a service at 0 is never called):")
    for service, limit in budget.items():
        print(f"  {service:<11}{limit}")

    recorder = Recorder(budget, send)
    problems: list[str] = []
    PIPELINE_RUNS.clear()
    with recorder.wrapping_the_app():
        for run in range(1, args.runs + 1):
            recorder.run = run
            code = runner(args.request)
            if code:
                problems.append(f"run {run} ended with code {code}")

    print_calls(recorder)
    if recorder.in_flight:
        print(f"Nothing was written: a call was still running after {WAIT_FOR_CALLS_S:g} seconds.")
        return 1
    if not any(recorder.used.values()):
        print("No calls were sent, so nothing was written. Check that the service has a key.")
        return 1
    problems += recorder.problems()
    if problems and not args.keep_failures:
        print("Nothing was written, because of:\n" + "\n".join(f"- {problem}" for problem in problems))
        refused = {c.service for c in recorder.calls if c.error and (c.service == "reddit" or c.targets_reddit)}
        if "reddit" in refused:
            print(REDDIT_SOURCE_HINT)
        for service in sorted(refused - {"reddit"}):
            print(f"The app tried to fetch a Reddit page through {service}, which is never allowed.")
        if refused:
            print(REFUSED_ROW_NOTE)
        for service in sorted({call.service for call in recorder.calls if call.over_budget}):
            print(f"To let the app call {service}, add --budget {service}=N.")
        print("To keep this recording as it is, add --keep-failures.")
        return 1

    # Read now, so whatever the run loaded from .env is included. A plain variable holding the very name given with
    # --by, such as USERNAME, is a person's name given on purpose.
    secrets = {name: value for name, value in secrets_in(env).items() if value != args.by or secret_named(name)}
    memory = RedditMemory(args.request)
    rows, marked = clean(list(recorder.calls), args.request, memory)
    pipeline = clean_pipeline([{"run": n, **r} for n, r in enumerate(PIPELINE_RUNS, 1)], memory, secrets)
    hidden = count_values([rows, pipeline], secrets)
    files, line = build_files(args, recorder, rows, problems, secrets, pipeline)
    index = index_text(root, args.name, line)
    leaks = sorted({name for text in [*files.values(), index] for name in find_leaks(text, secrets)})
    if leaks:
        print(f"Nothing was written: the values of these variables would have been: {', '.join(leaks)}")
        return 1
    write_files(root, args.name, files, index)
    if log is not None and log_path is not None:
        log_path.write_text(to_json(logged(log, sprint, today, args.by, args.name)), encoding="utf-8", newline="\n")

    if marked:
        print(f"Reddit text: {marked} LLM call(s) replaced whole. Read them before relying on this recording.")
    if hidden:
        print("Environment values replaced: " + ", ".join(f"{name} ({count})" for name, count in hidden.items()))
    print(f"Wrote {len(files)} files to {root / args.name}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
