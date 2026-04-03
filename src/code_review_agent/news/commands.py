"""REPL commands for the news reader.

news <domain>     -- fetch latest from domain
news add <url>    -- add custom RSS feed
news list         -- list configured domains
news stats        -- show read/saved/unread counts
read-news         -- open saved news navigator
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from rich.console import Console

from code_review_agent.news.domains import list_domains, resolve_domain
from code_review_agent.news.fetcher import fetch_news
from code_review_agent.news.storage import ArticleStore

if TYPE_CHECKING:
    from code_review_agent.interactive.session import SessionState
    from code_review_agent.news.models import Article

logger = structlog.get_logger(__name__)
console = Console()

_DEFAULT_DB = Path("~/.cra/reviews.db").expanduser()


def cmd_news(args: list[str], session: SessionState) -> None:
    """Handle the 'news' command and its subcommands."""
    if not args:
        console.print("  Usage: news <domain> | news list | news stats")
        console.print("  Example: news hackernews, news ai, news tech")
        return

    subcmd = args[0]

    if subcmd == "list":
        console.print(list_domains())
        return

    if subcmd == "stats":
        _show_stats()
        return

    if subcmd == "add" and len(args) >= 3:
        _add_custom_feed(args[1], args[2])
        return

    # Treat as domain name
    _fetch_domain(subcmd, session=session)


def cmd_read_news(args: list[str], session: SessionState) -> None:
    """Open the news navigator for browsing cached articles."""
    from code_review_agent.news.navigator import run_news_navigator

    store = ArticleStore(db_path=_DEFAULT_DB)
    domain = args[0] if args else None
    articles = store.load_articles(domain=domain, limit=200)

    if not articles:
        console.print("  No articles found. Fetch some first: news hackernews")
        return

    run_news_navigator(articles, store=store)


def _fetch_domain(domain_name: str, session: SessionState | None = None) -> None:
    """Fetch articles from a domain, curate with LLM, and save."""
    configs = resolve_domain(domain_name)
    if not configs:
        console.print(f"  Unknown domain: {domain_name}")
        console.print("  Use 'news list' to see available domains.")
        return

    console.print(f"  Fetching from {domain_name}...", end="")
    articles = fetch_news(domain_name)
    if not articles:
        console.print(" no articles found.")
        return

    console.print(f" {len(articles)} articles fetched.")

    # LLM curation: summarize and rank articles
    curated_articles = _curate_with_llm(articles, domain_name, session)

    store = ArticleStore(db_path=_DEFAULT_DB)
    to_save = curated_articles if curated_articles else articles
    count = store.save_articles(to_save)
    unread = store.get_unread_count(domain_name)
    console.print(f"  {count} articles saved ({unread} unread)")


def _curate_with_llm(
    articles: list[Article],
    domain: str,
    session: SessionState | None,
) -> list[Article]:
    """Use the LLM to curate and summarize articles.

    Returns articles enriched with LLM-generated summaries.
    Falls back to original articles on failure.
    """
    if session is None:
        return articles

    try:
        from code_review_agent.llm_client import LLMClient
        from code_review_agent.news.curator import curate_articles

        settings = session.effective_settings
        llm = LLMClient(settings)

        console.print("  Curating with LLM...", end="")
        response = curate_articles(articles, llm, domain)

        if not response.curated_articles:
            console.print(" no curation results.")
            return articles

        # Enrich original articles with LLM summaries
        enriched: list[Article] = []
        for curated in response.curated_articles:
            idx = curated.article_index
            if 0 <= idx < len(articles):
                original = articles[idx]
                enriched.append(
                    original.model_copy(
                        update={
                            "summary": curated.summary,
                            "tags": tuple(curated.tags[:5]),
                            "score": max(original.score, curated.relevance_score),
                        }
                    )
                )

        if response.synthesis:
            console.print(" done.")
            console.print(f"  [bold]Summary:[/bold] {response.synthesis}")
        else:
            console.print(f" {len(enriched)} curated.")

        return enriched if enriched else articles
    except Exception:
        logger.debug("LLM curation failed, using raw articles", exc_info=True)
        console.print(" skipped (LLM unavailable).")
        return articles


def _show_stats() -> None:
    """Show per-domain read/saved/unread counts."""
    store = ArticleStore(db_path=_DEFAULT_DB)
    stats = store.get_stats()
    if not stats:
        console.print("  No articles. Fetch some first: news hackernews")
        return

    console.print("\n  Domain stats:")
    for domain, counts in stats.items():
        total = counts["total"]
        read = counts["read"]
        saved = counts["saved"]
        rate = f"{read * 100 // total}%" if total > 0 else "0%"
        console.print(
            f"    {domain:<20} {total:>4} fetched, {read:>4} read, {saved:>4} saved ({rate})"
        )


def _add_custom_feed(name: str, url: str) -> None:
    """Add a custom RSS feed to config."""
    console.print(f"  Added custom feed: {name} -> {url}")
    console.print("  Custom feed persistence coming soon.")
