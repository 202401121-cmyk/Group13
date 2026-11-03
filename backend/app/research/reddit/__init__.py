"""Reddit as a research source (US-12): community posts and comments, with their permalinks.

from app.research.reddit import RequestBudget, subreddits_for
result = reddit.search_threads("laptop for coding", subreddits_for("laptop"), budget=RequestBudget())

The research stage stays a stub until its own story wires this in; see base.py for the rules every source keeps.
"""

from .base import IntervalLimiter, RedditResult, RedditSource, RequestBudget, ThreadInfo
from .settings import RedditSettings, load_reddit_settings
from .subreddits import SubredditConfigError, load_subreddits, subreddits_for

__all__ = [
    "IntervalLimiter",
    "RedditResult",
    "RedditSettings",
    "RedditSource",
    "RequestBudget",
    "SubredditConfigError",
    "ThreadInfo",
    "load_reddit_settings",
    "load_subreddits",
    "subreddits_for",
]
