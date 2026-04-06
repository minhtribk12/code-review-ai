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
        _add_custom_feed(args[1], args[2], session)
        return

    if subcmd == "remove" and len(args) >= 2:
        _remove_custom_feed(args[1], session)
        return

    if subcmd == "refresh":
        _refresh(session)
        return

    if subcmd == "cleanup":
        _cleanup()
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

    # Pass source status from last fetch if available
    source_status: dict[str, str] | None = None
    news_bg = getattr(session, "_news_fetch", None)
    if news_bg is not None and news_bg.is_done:
        source_status = news_bg.source_status

    run_news_navigator(articles, store=store, source_status=source_status)


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

    bg = BackgroundNewsFetch(domain=topic, session=session, depth=depth)

    session._news_fetch = bg  # type: ignore[attr-defined]

    prompt_app = getattr(session, "_prompt_session", None)
    if prompt_app is not None:
        bg._prompt_app = prompt_app.app

    bg.start()

    mode = "30-day deep research" if depth == "deep" else "multi-source brief"
    console.print(f"  {mode}: '{topic}' (HN + Reddit + Web)")
    console.print("  Progress in toolbar. Use 'read-news' when done.")


def _show_stats() -> None:
    """Show per-domain read/saved/unread counts + cache size."""
    store = ArticleStore(db_path=_DEFAULT_DB)
    stats = store.get_stats()
    if not stats:
        console.print("  No articles. Fetch some first: news ai")
        return

    console.print("\n  Domain stats:")
    total_all = 0
    for domain, counts in stats.items():
        total = counts["total"]
        read = counts["read"]
        saved = counts["saved"]
        total_all += total
        rate = f"{read * 100 // total}%" if total > 0 else "0%"
        console.print(
            f"    {domain:<20} {total:>4} fetched, {read:>4} read, {saved:>4} saved ({rate})"
        )

    # Cache size
    import os

    db_size = 0
    if _DEFAULT_DB.is_file():
        db_size = os.path.getsize(_DEFAULT_DB)
    size_str = f"{db_size / 1024:.0f}KB" if db_size < 1048576 else f"{db_size / 1048576:.1f}MB"
    console.print(f"\n  Total: {total_all} articles | DB size: {size_str}")
    console.print(f"  Unread: {store.get_unread_count()}")

    # Weekly summary
    weekly = store.get_weekly_summary()
    w_read = weekly["read_this_week"]
    w_saved = weekly["saved_this_week"]
    w_domain = weekly["top_domain"]
    w_count = weekly["top_domain_count"]
    console.print(f"\n  This week: {w_read} read, {w_saved} saved")
    if w_domain:
        console.print(f"  Most read: {w_domain} ({w_count} articles)")


def _add_custom_feed(
    name: str,
    url: str,
    session: SessionState | None = None,
) -> None:
    """Add a custom RSS feed and persist to config.yaml."""
    if session is None:
        console.print("  Session required.")
        return
    try:
        config_store = session._get_config_store()
        data = config_store.load()
        feeds = data.get("news_feeds", [])
        if not isinstance(feeds, list):
            feeds = []
        # Check for duplicate
        if any(f.get("name") == name for f in feeds if isinstance(f, dict)):
            console.print(f"  Feed '{name}' already exists. Use 'news remove {name}' first.")
            return
        feeds.append({"name": name, "url": url})
        data["news_feeds"] = feeds
        config_store.save(data)
        console.print(f"  Added feed: {name} -> {url} (saved to config.yaml)")
    except Exception:
        logger.debug("failed to save custom feed", exc_info=True)
        console.print("  Failed to save feed.")


def _remove_custom_feed(name: str, session: SessionState | None = None) -> None:
    """Remove a custom RSS feed from config.yaml."""
    if session is None:
        console.print("  Session required.")
        return
    try:
        config_store = session._get_config_store()
        data = config_store.load()
        feeds = data.get("news_feeds", [])
        if not isinstance(feeds, list):
            console.print("  No custom feeds configured.")
            return
        new_feeds = [f for f in feeds if not (isinstance(f, dict) and f.get("name") == name)]
        if len(new_feeds) == len(feeds):
            console.print(f"  Feed '{name}' not found.")
            return
        data["news_feeds"] = new_feeds
        config_store.save(data)
        console.print(f"  Removed feed: {name}")
    except Exception:
        logger.debug("failed to remove custom feed", exc_info=True)
        console.print("  Failed to remove feed.")


def _refresh(session: SessionState | None = None) -> None:
    """Force re-fetch bypassing 24h cache."""
    if session is None:
        console.print("  Session required.")
        return
    # Clear the news fetch cache by removing the _news_fetch reference
    existing = getattr(session, "_news_fetch", None)
    if existing is not None and existing.is_running:
        console.print("  Fetch already in progress.")
        return
    # Delete cached articles to force fresh fetch
    store = ArticleStore(db_path=_DEFAULT_DB)
    store.cleanup_old(days=0)  # delete all unsaved
    session._news_fetch = None  # type: ignore[attr-defined]
    console.print("  Cache cleared. Run 'news <topic>' to fetch fresh.")


def _cleanup() -> None:
    """Delete unsaved articles older than 30 days."""
    store = ArticleStore(db_path=_DEFAULT_DB)
    count = store.cleanup_old(days=30)
    console.print(f"  Cleaned up {count} old articles.")
