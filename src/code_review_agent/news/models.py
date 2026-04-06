"""Article model for the news reader."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ArticlePriority(StrEnum):
    """Visual priority in the news list."""

    TRENDING = "trending"
    RECENT = "recent"
    SAVED = "saved"
    READ = "read"


class Article(BaseModel, frozen=True):
    """A single news article."""

    id: str  # unique: domain:external_id
    domain: str
    title: str
    url: str
    author: str | None = None
    published_at: datetime | None = None
    fetched_at: datetime = Field(default_factory=datetime.now)
    score: int = 0
    comment_count: int = 0
    comments_url: str | None = None
    tags: tuple[str, ...] = ()
    summary: str = ""
    content_html: str = ""
    content_text: str = ""
    is_read: bool = False
    is_saved: bool = False
    read_position: float = 0.0

    @property
    def priority(self) -> ArticlePriority:
        if self.is_saved:
            return ArticlePriority.SAVED
        if self.is_read:
            return ArticlePriority.READ
        if self.score >= 100:
            return ArticlePriority.TRENDING
        return ArticlePriority.RECENT

    @property
    def age_display(self) -> str:
        """Human-readable age string."""
        if self.published_at is None:
            return ""
        delta = datetime.now() - self.published_at
        hours = delta.total_seconds() / 3600
        if hours < 1:
            return f"{int(delta.total_seconds() / 60)}m"
        if hours < 24:
            return f"{int(hours)}h"
        days = int(hours / 24)
        if days < 30:
            return f"{days}d"
        return f"{int(days / 30)}mo"

    @property
    def score_display(self) -> str:
        """Formatted score (e.g., 1.2k)."""
        if self.score >= 1000:
            return f"{self.score / 1000:.1f}k"
        return str(self.score)
