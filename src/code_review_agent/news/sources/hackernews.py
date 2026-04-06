"""Hacker News adapter via Algolia API (free, no auth required).

Endpoint: hn.algolia.com/api/v1/search
Comment enrichment: hn.algolia.com/api/v1/items/{id}
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import httpx
import structlog

from code_review_agent.news.sources import RawNewsItem

if TYPE_CHECKING:
    from code_review_agent.news.query import ProcessedQuery

logger = structlog.get_logger(__name__)

_HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search"
_HN_ITEM_URL = "https://hn.algolia.com/api/v1/items/{object_id}"
_HITS_PER_PAGE = 30
_MIN_POINTS = 2
_COMMENT_ENRICHMENT_COUNT = 5
_COMMENT_WORKERS = 5
_MAX_COMMENTS_PER_ITEM = 5
_USER_AGENT = "CRA-NewsReader/1.0 (+https://github.com/minhtribk12/code-review-ai)"


def fetch(query: ProcessedQuery, *, timeout: int = 30) -> list[RawNewsItem]:
    """Fetch stories from Hacker News Algolia API. Never raises."""
    if not query.hn_query:
        return []

    start = time.monotonic()
    try:
        items = _search_stories(query.hn_query, timeout=timeout)
    except Exception:
        logger.error(
            "hn_fetch_failed",
            query=query.hn_query,
            recovery_action="returning_empty_list",
        )
        return []

    # Enrich top items with comments
    items_to_enrich = items[:_COMMENT_ENRICHMENT_COUNT]
    if items_to_enrich:
        _enrich_with_comments(items_to_enrich, timeout=max(5, timeout // 3))

    elapsed = time.monotonic() - start
    logger.info(
        "hn_fetch_complete",
        query=query.hn_query,
        items=len(items),
        enriched=len(items_to_enrich),
        elapsed_ms=round(elapsed * 1000, 1),
    )
    return items


def _search_stories(q: str, *, timeout: int = 30) -> list[RawNewsItem]:
    """Search HN stories via Algolia API."""
    from_ts = int((datetime.now() - timedelta(days=30)).timestamp())

    params: dict[str, str | int] = {
        "query": q,
        "tags": "story",
        "numericFilters": f"created_at_i>{from_ts},points>{_MIN_POINTS}",
        "hitsPerPage": _HITS_PER_PAGE,
    }

    logger.debug("hn_search_request", query=q, hits_per_page=_HITS_PER_PAGE)

    response = httpx.get(
        _HN_SEARCH_URL,
        params=params,
        headers={"User-Agent": _USER_AGENT},
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()

    hits = data.get("hits", [])
    logger.debug(
        "hn_search_response",
        status_code=response.status_code,
        items_returned=len(hits),
    )

    items: list[RawNewsItem] = []
    for hit in hits:
        created = hit.get("created_at_i")
        published = datetime.fromtimestamp(created) if created else None

        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
        hn_url = f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"

        items.append(
            RawNewsItem(
                source="hackernews",
                external_id=str(hit.get("objectID", "")),
                title=hit.get("title", ""),
                url=url,
                author=hit.get("author"),
                published_at=published,
                score=hit.get("points", 0),
                comment_count=hit.get("num_comments", 0),
                comments_url=hn_url,
                summary="",
                tags=(),
                date_confidence="high",
            )
        )

    return items


def _enrich_with_comments(items: list[RawNewsItem], *, timeout: int = 10) -> None:
    """Fetch top comments for items (modifies list in-place via replacement)."""

    def _fetch_comments(item: RawNewsItem) -> RawNewsItem:
        try:
            url = _HN_ITEM_URL.format(object_id=item.external_id)
            resp = httpx.get(url, headers={"User-Agent": _USER_AGENT}, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()

            children = data.get("children", [])
            comments: list[str] = []
            for child in children[:_MAX_COMMENTS_PER_ITEM]:
                text = child.get("text", "")
                if text:
                    clean = _strip_html(text)[:200]
                    if clean:
                        comments.append(clean)

            if comments:
                return RawNewsItem(
                    source=item.source,
                    external_id=item.external_id,
                    title=item.title,
                    url=item.url,
                    author=item.author,
                    published_at=item.published_at,
                    score=item.score,
                    comment_count=item.comment_count,
                    comments_url=item.comments_url,
                    summary=item.summary,
                    tags=item.tags,
                    top_comments=tuple(comments),
                    date_confidence=item.date_confidence,
                )
        except Exception:
            logger.debug("hn_comment_fetch_failed", item_id=item.external_id)
        return item

    with ThreadPoolExecutor(max_workers=_COMMENT_WORKERS) as pool:
        enriched = list(pool.map(_fetch_comments, items))

    for i, enriched_item in enumerate(enriched):
        items[i] = enriched_item


def _strip_html(text: str) -> str:
    """Strip HTML tags from text."""
    clean = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", clean).strip()


def health_check() -> bool:
    """Quick connectivity test for HN Algolia."""
    try:
        resp = httpx.head(
            _HN_SEARCH_URL,
            headers={"User-Agent": _USER_AGENT},
            timeout=2,
        )
        return resp.status_code < 500
    except Exception:
        return False
