"""Generic RSS/Atom feed adapter.

Handles all RSS 2.0 and Atom feeds via feedparser.
Covers ~95% of all news sources.
"""

from __future__ import annotations

from datetime import datetime
from time import mktime
from typing import TYPE_CHECKING

import feedparser
import structlog

if TYPE_CHECKING:
    from code_review_agent.news.domains import DomainConfig

from code_review_agent.news.models import Article

logger = structlog.get_logger(__name__)

_USER_AGENT = "CRA-NewsReader/1.0 (+https://github.com/minhtribk12/code-review-ai)"


def fetch_rss(config: DomainConfig, *, max_items: int = 30) -> list[Article]:
    """Fetch articles from an RSS or Atom feed.

    Returns a list of Article objects parsed from the feed entries.
    """
    try:
        feed = feedparser.parse(config.url, agent=_USER_AGENT)
    except Exception:
        logger.warning(f"failed to fetch feed {config.url}")
        return []

    if feed.bozo and not feed.entries:
        logger.debug(f"feed parse error for {config.name}: {feed.bozo_exception}")
        return []

    articles: list[Article] = []
    now = datetime.now()

    for entry in feed.entries[:max_items]:
        article_id = f"{config.name}:{_extract_id(entry)}"
        published = _parse_date(entry)
        title = _clean_text(entry.get("title", "Untitled"))
        url = entry.get("link", "")
        author = entry.get("author", entry.get("dc_creator"))
        summary = _clean_text(entry.get("summary", ""))[:300]

        # Extract tags
        tags: list[str] = []
        for tag in entry.get("tags", []):
            term = tag.get("term", "")
            if term:
                tags.append(term.lower())

        articles.append(
            Article(
                id=article_id,
                domain=config.name,
                title=title,
                url=url,
                author=author,
                published_at=published,
                fetched_at=now,
                score=0,
                comment_count=0,
                tags=tuple(tags[:5]),
                summary=summary,
            )
        )

    logger.debug(f"fetched {len(articles)} articles from {config.name}")
    return articles


def _extract_id(entry: dict[str, object]) -> str:
    """Extract a unique ID from a feed entry."""
    # Prefer guid/id, fall back to link
    entry_id = entry.get("id", entry.get("guid", entry.get("link", "")))
    return str(entry_id).split("/")[-1][:64] if entry_id else str(hash(str(entry)))


def _parse_date(entry: dict[str, object]) -> datetime | None:
    """Parse published date from feed entry."""
    for field in ("published_parsed", "updated_parsed"):
        parsed = entry.get(field)
        if parsed:
            try:
                return datetime.fromtimestamp(mktime(parsed))  # type: ignore[arg-type]
            except (TypeError, ValueError, OverflowError):
                continue
    return None


def _clean_text(text: str) -> str:
    """Strip HTML tags from text for summary display."""
    import re

    clean = re.sub(r"<[^>]+>", "", text)
    clean = re.sub(r"\s+", " ", clean)
    return clean.strip()
