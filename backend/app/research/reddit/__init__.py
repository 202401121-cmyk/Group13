"""Reddit as a research source (US-12): community posts and comments, with their permalinks.

from app.research.reddit import RequestBudget, build_reddit_source, subreddits_for
reddit = build_reddit_source()  # None when REDDIT_SOURCE=off
result = reddit.search_threads("laptop for coding", subreddits_for("laptop"), budget=RequestBudget())

The research stage stays a stub until its own story wires this in; see base.py for the rules every source keeps.
"""

from .archive import ArchiveRedditSource
from .arctic_shift import ArcticShiftSource
from .base import IntervalLimiter, RedditResult, RedditSource, RequestBudget, ThreadInfo
from .factory import build_reddit_source
from .pullpush import PullPushSource
from .settings import RedditSettings, load_reddit_settings
from .subreddits import SubredditConfigError, load_subreddits, subreddits_for

__all__ = [
    "ArchiveRedditSource",
    "ArcticShiftSource",
    "IntervalLimiter",
    "PullPushSource",
    "RedditResult",
    "RedditSettings",
    "RedditSource",
    "RequestBudget",
    "SubredditConfigError",
    "ThreadInfo",
    "build_reddit_source",
    "load_reddit_settings",
    "load_subreddits",
    "subreddits_for",
]
