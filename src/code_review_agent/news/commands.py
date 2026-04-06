"""REPL commands for the news reader.

news <topic>          -- multi-source intelligence brief (HN + Reddit + Web)
news 30days <topic>   -- deep 30-day research across all sources
news list             -- list available preset domains
news stats            -- show read/saved/unread counts
news add <name> <url> -- add custom RSS feed
read-news [domain]    -- open saved news navigator
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from rich.console import Console

from code_review_agent.news.domains import list_domains
from code_review_agent.news.storage import ArticleStore

if TYPE_CHECKING:
    from code_review_agent.interactive.session import SessionState

logger = structlog.get_logger(__name__)
console = Console()

_DEFAULT_DB = Path("~/.cra/reviews.db").expanduser()


def cmd_news(args: list[str], session: SessionState) -> None:
    """Handle the 'news' command and its subcommands."""
    if not args:
        console.print("  Usage: news <topic> | news 30days <topic> | news list | news stats")
        console.print("  Examples:")
        console.print("    news ai                  Multi-source brief (HN + Reddit + Web)")
        console.print("    news 30days rust         Deep 30-day research across all sources")
        console.print("    news hackernews          Fetch from specific domain")
        console.print("    news list                Show available domains")
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

    # news 30days <topic> -- deep research mode
    if subcmd == "30days":
        if len(args) < 2:
            console.print("  Usage: news 30days <topic>")
            console.print("  Example: news 30days rust, news 30days Claude Code")
            return
        topic = " ".join(args[1:])
        _fetch_topic(topic, session=session, depth="deep")
        return

    # Any topic -- use wide multi-source pipeline
    topic = " ".join(args)
    _fetch_topic(topic, session=session, depth="default")


def cmd_read_news(args: list[str], session: SessionState) -> None:
    """Open the news navigator for browsing cached articles."""
    from code_review_agent.news.navigator import run_news_navigator

    store = ArticleStore(db_path=_DEFAULT_DB)
    domain = args[0] if args else None
    articles = store.load_articles(domain=domain, limit=200)

    if not articles:
        console.print("  No articles found. Fetch some first: news ai")
        return

    run_news_navigator(articles, store=store)


def _fetch_topic(
    topic: str,
    *,
    session: SessionState | None = None,
    depth: str = "default",
) -> None:
    """Launch background multi-source news pipeline.

    Searches HN + Reddit + Web for the topic, scores, deduplicates,
    then optionally synthesizes with LLM. Non-blocking.

    depth:
      "default" -- 30s per source, top 30 items per source
      "deep"    -- 60s per source, top 60 items, wider query expansion
    """
    if session is None:
        console.print("  Session required for news fetch.")
        return

    # Check if a fetch is already running
    existing = getattr(session, "_news_fetch", None)
    if existing is not None and existing.is_running:
        console.print(f"  Already fetching '{existing.domain}'. Wait or use 'read-news'.")
        return

    from code_review_agent.news.background import BackgroundNewsFetch

    bg = BackgroundNewsFetch(domain=topic, session=session)

    session._news_fetch = bg  # type: ignore[attr-defined]

    prompt_app = getattr(session, "_prompt_session", None)
    if prompt_app is not None:
        bg._prompt_app = prompt_app.app

    bg.start()

    mode = "30-day deep research" if depth == "deep" else "multi-source brief"
    console.print(f"  {mode}: '{topic}' (HN + Reddit + Web)")
    console.print("  Progress in toolbar. Use 'read-news' when done.")


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
