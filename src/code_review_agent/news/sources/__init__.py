"""Source adapter protocol and canonical data types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True)
class RawNewsItem:
    """Canonical news item from any source adapter."""

    source: str
    external_id: str
    title: str
    url: str
    author: str | None = None
    published_at: datetime | None = None
    score: int = 0
    comment_count: int = 0
    comments_url: str | None = None
    summary: str = ""
    tags: tuple[str, ...] = ()
    top_comments: tuple[str, ...] = ()
    date_confidence: str = "high"  # high, med, low
    subreddit: str | None = None  # Reddit-specific
