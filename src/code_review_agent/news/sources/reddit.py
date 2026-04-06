"""Reddit adapter via public JSON API (free, no auth, requires User-Agent).

Endpoint: reddit.com/search.json
Rate limit: ~1 request per 2 seconds.
"""

from __future__ import annotations

import html
import time
from datetime import datetime
from typing import TYPE_CHECKING

import httpx
import structlog

from code_review_agent.news.sources import RawNewsItem

if TYPE_CHECKING:
    from code_review_agent.news.query import ProcessedQuery

logger = structlog.get_logger(__name__)

_REDDIT_SEARCH_URL = "https://www.reddit.com/search.json"
_USER_AGENT = "CRA-NewsReader/1.0 (+https://github.com/minhtribk12/code-review-ai)"
_RATE_LIMIT_DELAY = 2.0
_MAX_RESULTS = 25


def fetch(query: ProcessedQuery, *, timeout: int = 30) -> list[RawNewsItem]:
    """Fetch posts from Reddit public JSON API. Never raises."""
    if not query.reddit_query:
        return []

    start = time.monotonic()
    try:
        items = _search_reddit(
            query.reddit_query,
            sort="relevance",
            time_filter="month",
            limit=_MAX_RESULTS,
            timeout=timeout,
        )
    except Exception as exc:
        logger.error(
            "reddit_fetch_failed",
            error_type=type(exc).__name__,
            error_message=str(exc)[:100],
            query=query.reddit_query,
            recovery_action="returning_empty_list",
        )
        return []

    elapsed = time.monotonic() - start
    logger.info(
        "reddit_fetch_complete",
        query=query.reddit_query,
        items=len(items),
        elapsed_ms=round(elapsed * 1000, 1),
    )
    return items


def _search_reddit(
    q: str,
    *,
    sort: str = "relevance",
    time_filter: str = "month",
    limit: int = 25,
    timeout: int = 30,
) -> list[RawNewsItem]:
    """Execute Reddit search and parse results."""
    params: dict[str, str | int] = {
        "q": q,
        "sort": sort,
        "t": time_filter,
        "limit": limit,
        "type": "link",
    }

    logger.debug("reddit_search_request", query=q, sort=sort, time_filter=time_filter)

    response = httpx.get(
        _REDDIT_SEARCH_URL,
        params=params,
        headers={"User-Agent": _USER_AGENT},
        timeout=timeout,
    )

    if response.status_code == 429:
        logger.warning(
            "reddit_rate_limited",
            status_code=429,
            recovery_action="returning_empty_after_delay",
        )
        time.sleep(_RATE_LIMIT_DELAY)
        return []

    response.raise_for_status()
    data = response.json()

    children = data.get("data", {}).get("children", [])
    logger.debug(
        "reddit_search_response",
        status_code=response.status_code,
        items_returned=len(children),
    )

    items: list[RawNewsItem] = []
    for child in children:
        post = child.get("data", {})
        if not post:
            continue

        created_utc = post.get("created_utc")
        published = datetime.fromtimestamp(created_utc) if created_utc else None

        permalink = post.get("permalink", "")
        comments_url = f"https://www.reddit.com{permalink}" if permalink else None

        # Use the external URL if available, otherwise the Reddit comments page
        url = post.get("url", "")
        if not url or url == permalink or "reddit.com" in url:
            url = comments_url or ""

        subreddit = post.get("subreddit", "")
        selftext = html.unescape(post.get("selftext", ""))[:300]

        items.append(
            RawNewsItem(
                source="reddit",
                external_id=post.get("id", ""),
                title=html.unescape(post.get("title", "")),
                url=url,
                author=post.get("author"),
                published_at=published,
                score=post.get("score", 0),
                comment_count=post.get("num_comments", 0),
                comments_url=comments_url,
                summary=selftext,
                tags=(f"r/{subreddit}",) if subreddit else (),
                date_confidence="high",
                subreddit=subreddit,
            )
        )

    return items


def health_check() -> bool:
    """Quick connectivity test for Reddit."""
    try:
        resp = httpx.head(
            "https://www.reddit.com/.json",
            headers={"User-Agent": _USER_AGENT},
            timeout=2,
        )
        return resp.status_code < 500
    except Exception:
        return False
