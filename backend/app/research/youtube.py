"""Review videos from the YouTube Data API (US-38): titles, descriptions and top comments, never a transcript.

One search a search: search.list (type video, relevance, India) with a query built from the rubric's known words only,
never the shopper's text. Then one videos.list call for the chosen 3 to 5, and one commentThreads.list call for each
(the top 20 by relevance, text only). docs/api-setup.md has YouTube's terms (W2): titles, descriptions and comments
are allowed, transcripts are not, so no captions endpoint is ever called.

Each video is one source. Its text is the title, the channel that made it, the description, then the viewers' comments,
each its own paragraph with a separator between them. It is built to fit the source cap (max_source_tokens), so the
stage's capping never drops the comments wholesale: the description takes at most a third, and comments are added
while they fit. Comments are viewers' own words, so a video with comments carries the reddit data class
(P-23): only Groq reads it, it is kept at most 24 hours (the cache's serve limit), and retention.py strips it from a
saved search. A video with comments off, or whose comments could not be read, is a public source.

No commenter is ever named: the author fields are never read; in a comment, an email address or an @handle (any
script, "@ name" too) becomes "a viewer", and any link becomes "a link", as a link can name a person's profile on
any site. "@12999" is a price, so it stays. The video's own channel is a publisher, like a review site, so its
name is kept.

Caps (config/research.yaml): youtube_search counts search.list calls (YouTube allows 100 a day; the team's cap is 80),
youtube counts quota units (videos.list and commentThreads.list cost 1 each, from 10,000 a day). Each per-search cap
leaves room for the one retry a failed call gets (US-31), which counts like any call. A repeat search is served from
the cache and costs nothing. Comments are read only while a whole call still fits before the stage's deadline, so
the videos already read are returned, never lost to a timeout. The key travels only in a header.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import ValidationError

from app.contracts import DataClass, Source, SourceFailure, SourceType
from app.contracts.research import DEFAULT_TIER, MAX_SOURCE_CHARS
from app.llm.fitting import estimate_tokens
from app.llm.retry import RetryClock

from .cache import ResearchCache
from .capping import cap_text
from .cleaning import clean_text
from .reddit.base import FailureReason
from .retrying import send_once_more
from .usage import SearchUsage

log = logging.getLogger(__name__)

API = "https://www.googleapis.com/youtube/v3"
REGION = "IN"
TIMEOUT_S = 10.0
MAX_VIDEOS = 5
COMMENTS_PER_VIDEO = 20
SEARCH_CAP = "youtube_search"  # search.list calls
UNITS_CAP = "youtube"  # quota units: videos.list and commentThreads.list cost 1 each
COMMENT_SEPARATOR = "• • •"  # a paragraph between two viewers' comments: a quote can't run from one into the next
MAX_SOURCE_TOKENS = 3000  # research.yaml's max_source_tokens: the stage caps every source to this
DESCRIPTION_SHARE = 3  # the description takes at most a third of that, so comments always have room
COMMENTS_HEADING = "Viewer comments:"
A_VIEWER = "a viewer"
_QUOTA = {"quotaExceeded", "dailyLimitExceeded"}
_RATE = {"rateLimitExceeded", "userRateLimitExceeded"}
# A viewer's handle or channel, written in a comment: "@priya.k", "youtube.com/@priya", "youtube.com/channel/UC...".
_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
# A link of any site: with a scheme or www, or a bare domain with a path (instagram.com/@x, youtube.com/SomeName).
_LINK = re.compile(
    r"(?:https?://|www\.)\S+|\b(?:[\w\-]+\.)+(?:com|in|net|org|io|co|me|tv|be|ly|gg|app|uk)/\S*", re.IGNORECASE
)
# A handle: one or more @, maybe a space, then a name in any script, up to a space or a stop. Never a number.
_HANDLE_CHARS = r"[^\s@,;:!?()\[\]{}<>\"'“”‘’]"
_HANDLE = re.compile(rf"@+ ?(?!\d){_HANDLE_CHARS}*(?<!\.)(?<!…)")


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class YouTubeResult:
    sources: tuple[Source, ...] = ()
    failure: SourceFailure | None = None
    from_cache: bool = False


@dataclass(frozen=True)
class _Video:
    id: str
    title: str
    channel: str
    description: str
    published_at: datetime | None


class _Refused(Exception):
    """A call that got no usable answer, with the reason the coverage note gives."""

    def __init__(self, reason: FailureReason, detail: str) -> None:
        super().__init__(detail)
        self.reason: FailureReason = reason
        self.detail = detail


def _failure(reason: FailureReason, detail: str) -> SourceFailure:
    return SourceFailure(source_type=SourceType.YOUTUBE, reason=reason, detail=detail)


class YouTubeSource:
    def __init__(
        self,
        http: httpx.Client,
        api_key: str | None,
        *,
        cache: ResearchCache,
        max_videos: int = MAX_VIDEOS,
        comments_per_video: int = COMMENTS_PER_VIDEO,
        max_tokens: int = MAX_SOURCE_TOKENS,
        now: Callable[[], datetime] = _utc_now,
        retry: RetryClock | None = None,
    ) -> None:
        self.http = http
        self._api_key = api_key
        self.cache = cache
        self.max_videos = max_videos
        self.comments_per_video = comments_per_video
        self.max_tokens = max_tokens
        self._now = now
        self.retry = retry if retry is not None else RetryClock()  # the one retry (US-31)

    def __repr__(self) -> str:
        return f"YouTubeSource(key_set={self._api_key is not None})"  # never the key, not even in a traceback

    def search(self, query: str, usage: SearchUsage) -> YouTubeResult:
        """Up to max_videos videos as sources. Never raises: a failure is named for the coverage note."""
        key = ("youtube", query, REGION)
        cached = self.cache.get_sources(key)
        if cached is not None:
            return YouTubeResult(cached, from_cache=True)
        if not self._api_key:
            return YouTubeResult(failure=_failure("blocked", "YouTube: YOUTUBE_API_KEY is not set"))
        try:
            ids = self._find(query, usage)
            videos = self._videos(ids, usage) if ids else []
        except _Refused as refused:
            return YouTubeResult(failure=_failure(refused.reason, refused.detail))
        if not videos:
            return YouTubeResult(failure=_failure("not_found", "YouTube: no video found"))
        sources: list[Source] = []
        failure: SourceFailure | None = None
        for video in videos:
            comments: list[str] = []
            if failure is None and self.retry.time_left() > TIMEOUT_S:  # else its comments are left for time
                try:
                    comments = self._comments(video.id, usage)
                except _Refused as refused:  # out of quota: the rest are read without their comments
                    failure = _failure(refused.reason, refused.detail)
            source = video_source(video, comments, self._now(), self.max_tokens)
            if source is not None:
                sources.append(source)
        if failure is None and sources:
            self.cache.put_sources(key, tuple(sources))  # never a failure: a later search may do better
        return YouTubeResult(tuple(sources), failure)

    # --- the three calls --------------------------------------------------------------------------------------------

    def _find(self, query: str, usage: SearchUsage) -> list[str]:
        params = {
            "part": "snippet",
            "type": "video",
            "order": "relevance",
            "regionCode": REGION,
            "maxResults": str(self.max_videos),
            "q": query,
        }
        reply = self._get("search", params, SEARCH_CAP, usage)
        items = reply.get("items")
        ids: list[str] = []
        for item in items if isinstance(items, list) else []:
            found = item.get("id") if isinstance(item, dict) else None
            video_id = found.get("videoId") if isinstance(found, dict) else None
            if isinstance(video_id, str) and re.fullmatch(r"[\w\-]{6,20}", video_id) and video_id not in ids:
                ids.append(video_id)
        return ids[: self.max_videos]

    def _videos(self, ids: list[str], usage: SearchUsage) -> list[_Video]:
        reply = self._get("videos", {"part": "snippet", "id": ",".join(ids)}, UNITS_CAP, usage)
        items = reply.get("items")
        by_id: dict[str, _Video] = {}
        for item in items if isinstance(items, list) else []:
            video = _video(item)
            if video is not None:
                by_id[video.id] = video
        return [by_id[i] for i in ids if i in by_id]  # in search's order of relevance

    def _comments(self, video_id: str, usage: SearchUsage) -> list[str]:
        """The top comments' text. Comments off, or any failure but running out of quota, gives none."""
        params = {
            "part": "snippet",
            "videoId": video_id,
            "order": "relevance",
            "maxResults": str(self.comments_per_video),
            "textFormat": "plainText",
        }
        try:
            reply = self._get("commentThreads", params, UNITS_CAP, usage)
        except _Refused as refused:
            if refused.reason == "quota_exhausted":
                raise
            return []  # comments off, or this one video's comments failed: it is read without them
        items = reply.get("items")
        texts = [text for item in items if (text := _comment_text(item))] if isinstance(items, list) else []
        return texts[: self.comments_per_video]

    def _get(self, endpoint: str, params: dict[str, str], cap: str, usage: SearchUsage) -> dict[str, Any]:
        if not usage.take(cap):
            raise _Refused("quota_exhausted", f"YouTube: the {cap} cap is reached")

        def send() -> httpx.Response:
            headers = {"X-Goog-Api-Key": self._api_key or "", "Accept": "application/json"}
            return self.http.get(f"{API}/{endpoint}", params=params, headers=headers, timeout=TIMEOUT_S)

        # One retry after a timeout, a 5xx or a connection error (US-31). A retry costs what the call costs.
        response = send_once_more(send, self.retry, TIMEOUT_S, lambda: usage.take(cap), "YouTube")
        if isinstance(response, httpx.TimeoutException):
            raise _Refused("timeout", f"YouTube {endpoint}: no reply within {TIMEOUT_S:g}s")
        if isinstance(response, httpx.HTTPError):
            raise _Refused("error", f"YouTube {endpoint}: request failed ({type(response).__name__})")
        status = response.status_code
        if not 200 <= status < 300:
            reason = _reason_of(response)
            raise _Refused(reason, f"YouTube {endpoint}: HTTP {status}")
        try:
            reply = response.json()
        except ValueError:
            raise _Refused("error", f"YouTube {endpoint}: the reply was not JSON") from None
        if not isinstance(reply, dict):
            raise _Refused("error", f"YouTube {endpoint}: the reply was not an object")
        return reply


def _reason_of(response: httpx.Response) -> FailureReason:
    """Google names why it refused in error.errors[].reason. Comments off is "refused", which _comments forgives."""
    try:
        errors = response.json().get("error", {}).get("errors", [])
        reasons = {e.get("reason") for e in errors if isinstance(e, dict)}
    except (ValueError, AttributeError):
        reasons = set()
    if reasons & _QUOTA:
        return "quota_exhausted"
    if reasons & _RATE or response.status_code == 429:
        return "rate_limited"
    if response.status_code in (401, 403):
        return "blocked"
    return "error"


def _video(item: Any) -> _Video | None:
    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
        return None
    snippet = item.get("snippet")
    if not isinstance(snippet, dict):
        return None
    title = _plain(snippet.get("title"))
    if not title:
        return None
    return _Video(
        id=item["id"],
        title=title,
        channel=_plain(snippet.get("channelTitle")),
        description=_plain(snippet.get("description"), keep_lines=True),
        published_at=_when(snippet.get("publishedAt")),
    )


def _comment_text(item: Any) -> str:
    """Only the comment's own words. Its author's name, channel and picture are never read."""
    try:
        snippet = item["snippet"]["topLevelComment"]["snippet"]
    except (KeyError, TypeError):
        return ""
    raw = snippet.get("textOriginal") if isinstance(snippet, dict) else None
    if not isinstance(raw, str):
        raw = snippet.get("textDisplay") if isinstance(snippet, dict) else None
    return without_viewers(_plain(raw, keep_lines=True)) if isinstance(raw, str) else ""


def without_viewers(text: str) -> str:
    """A comment with no viewer named in it: emails and @handles become "a viewer", and links "a link"."""
    text = _EMAIL.sub(A_VIEWER, text)
    text = _LINK.sub("a link", text)
    return _HANDLE.sub(A_VIEWER, text).strip()


def video_source(
    video: _Video, comments: list[str], fetched_at: datetime, max_tokens: int = MAX_SOURCE_TOKENS
) -> Source | None:
    """One source: the title, the channel, the description, then each comment its own paragraph, set apart. It fits
    `max_tokens`, so the stage's cap (whole paragraphs until the first that doesn't fit) leaves it whole. Reddit class
    with comments."""
    head = [video.title]
    if video.channel:
        head.append(f"Video from the channel {video.channel}.")
    if video.description:
        head.append(cap_text(video.description, max(1, max_tokens // DESCRIPTION_SHARE)))
    body = cap_text("\n\n".join(part for part in head if part), max_tokens)
    kept: list[str] = []
    for comment in comments:
        part = f"\n\n{COMMENTS_HEADING}\n\n{comment}" if not kept else f"\n\n{COMMENT_SEPARATOR}\n\n{comment}"
        if estimate_tokens(body + part) > max_tokens or len(body) + len(part) > MAX_SOURCE_CHARS:
            continue  # a shorter comment further down may still fit
        body += part
        kept.append(comment)
    url = f"https://www.youtube.com/watch?v={video.id}"
    try:
        return Source(
            id=f"src_{hashlib.sha256(url.encode()).hexdigest()[:16]}",
            url=url,
            type=SourceType.YOUTUBE,
            tier=DEFAULT_TIER[SourceType.YOUTUBE],
            title=video.title[:300],
            text=body,
            text_hash=hashlib.sha256(body.encode()).hexdigest(),
            fetched_at=fetched_at,
            published_at=video.published_at,
            site="youtube.com",
            via="api",
            data_class=DataClass.REDDIT if kept else DataClass.PUBLIC,
        )
    except (ValidationError, ValueError):  # skip the video, never crash the search
        log.info("research: a YouTube video could not be made a source")
        return None


def _plain(value: Any, *, keep_lines: bool = False) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = clean_text(value)
    if keep_lines:
        lines = [" ".join(line.split()) for line in cleaned.splitlines()]
        return "\n".join(line for line in lines if line).strip()
    return " ".join(cleaned.split())


def _when(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
