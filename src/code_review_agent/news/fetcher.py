"""News fetcher: coordinate domain resolution and adapter dispatch."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from code_review_agent.news.adapters.rss import fetch_rss
from code_review_agent.news.domains import resolve_domain

if TYPE_CHECKING:
    from code_review_agent.news.models import Article

logger = structlog.get_logger(__name__)


def fetch_news(domain_name: str, *, max_items: int = 30) -> list[Article]:
    """Fetch articles from a domain name (or meta-domain).

    Resolves the domain, dispatches to the appropriate adapter,
    and returns a merged list of articles sorted by score.
    """
    configs = resolve_domain(domain_name)
    if not configs:
        logger.warning(f"unknown domain: {domain_name}")
        return []

    all_articles: list[Article] = []
    for config in configs:
        articles = fetch_rss(config, max_items=max_items)
        all_articles.extend(articles)

    return sorted(all_articles, key=lambda a: a.score, reverse=True)
