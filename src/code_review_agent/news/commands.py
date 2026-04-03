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
from code_review_agent.news.storage import ArticleStore

if TYPE_CHECKING:
    from code_review_agent.interactive.session import SessionState

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
    """Launch background news fetch + LLM curation.

    Runs in a daemon thread so the REPL stays responsive.
    Progress is shown in the toolbar status bar.
    """
    configs = resolve_domain(domain_name)
    if not configs:
        console.print(f"  Unknown domain: {domain_name}")
        console.print("  Use 'news list' to see available domains.")
        return

    if session is None:
        console.print("  Session required for background fetch.")
        return

    # Check if a fetch is already running
    existing = getattr(session, "_news_fetch", None)
    if existing is not None and existing.is_running:
        console.print(f"  Already fetching {existing.domain}. Wait or use 'read-news'.")
        return

    from code_review_agent.news.background import BackgroundNewsFetch

    bg = BackgroundNewsFetch(domain=domain_name, session=session)

    # Store on session so toolbar can read status and completion can be detected
    session._news_fetch = bg  # type: ignore[attr-defined]

    # Wire up prompt app for toolbar refresh (same pattern as background review)
    prompt_app = getattr(session, "_prompt_session", None)
    if prompt_app is not None:
        bg._prompt_app = prompt_app.app

    bg.start()
    console.print(
        f"  Fetching {domain_name} in background. "
        f"Status shown in toolbar. Use 'read-news' when done."
    )


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
