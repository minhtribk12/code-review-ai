"""Web search adapter via httpx (free fallback using DuckDuckGo HTML).

Provides web search results when no Exa/Brave API key is configured.
Uses DuckDuckGo HTML search as a zero-config fallback.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING

import httpx
import structlog

from code_review_agent.news.sources import RawNewsItem

if TYPE_CHECKING:
    from code_review_agent.news.query import ProcessedQuery

logger = structlog.get_logger(__name__)

_DDG_URL = "https://html.duckduckgo.com/html/"
_USER_AGENT = "CRA-NewsReader/1.0 (+https://github.com/minhtribk12/code-review-ai)"
_MAX_RESULTS = 15


def fetch(query: ProcessedQuery, *, timeout: int = 30) -> list[RawNewsItem]:
    """Fetch web search results. Never raises."""
    if not query.web_query:
        return []

    start = time.monotonic()
    try:
        items = _search_ddg(query.web_query, timeout=timeout)
    except Exception as exc:
        logger.error(
            "web_fetch_failed",
            error_type=type(exc).__name__,
            error_message=str(exc)[:100],
            recovery_action="returning_empty_list",
        )
        return []

    elapsed = time.monotonic() - start
    logger.info(
        "web_fetch_complete",
        query=query.web_query,
        items=len(items),
        elapsed_ms=round(elapsed * 1000, 1),
    )
    return items


def _search_ddg(q: str, *, timeout: int = 30) -> list[RawNewsItem]:
    """Search DuckDuckGo HTML and parse results."""
    response = httpx.post(
        _DDG_URL,
        data={"q": q, "b": ""},
        headers={"User-Agent": _USER_AGENT},
        timeout=timeout,
        follow_redirects=True,
    )
    response.raise_for_status()

    items: list[RawNewsItem] = []
    # Parse result links from HTML
    # DDG HTML format: <a rel="nofollow" class="result__a" href="...">title</a>
    # <a class="result__snippet">snippet</a>
    results = re.findall(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'class="result__snippet"[^>]*>(.*?)</a>',
        response.text,
        re.DOTALL,
    )

    for i, (url, title_html, snippet_html) in enumerate(results[:_MAX_RESULTS]):
        # Clean HTML from title and snippet
        title = re.sub(r"<[^>]+>", "", title_html).strip()
        snippet = re.sub(r"<[^>]+>", "", snippet_html).strip()

        if not title or not url:
            continue

        # Extract domain for source info
        domain_match = re.search(r"https?://([^/]+)", url)
        domain = domain_match.group(1) if domain_match else "web"

        items.append(
            RawNewsItem(
                source="web",
                external_id=f"web-{i}",
                title=title,
                url=url,
                summary=snippet,
                tags=(domain,),
                date_confidence="low",
            )
        )

    logger.debug("ddg_search_response", items_returned=len(items))
    return items


def health_check() -> bool:
    """Quick connectivity test for DuckDuckGo."""
    try:
        resp = httpx.head(
            "https://html.duckduckgo.com/",
            headers={"User-Agent": _USER_AGENT},
            timeout=2,
        )
        return resp.status_code < 500
    except Exception:
        return False
