"""REPL middleware chain: pre/post command hooks.

Inspired by DeerFlow's 12-step middleware architecture.
Each middleware runs before and after every REPL command.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from code_review_agent.interactive.session import SessionState

logger = structlog.get_logger(__name__)


class Middleware:
    """Base middleware. Override pre_command and/or post_command."""

    def pre_command(self, command: str, args: list[str], session: SessionState) -> bool:
        """Run before command. Return False to block execution."""
        return True

    def post_command(self, command: str, args: list[str], session: SessionState) -> None:
        """Run after command completes."""


class MiddlewareChain:
    """Executes a list of middleware in order around each command."""

    def __init__(self, middlewares: list[Middleware] | None = None) -> None:
        self._middlewares = middlewares or []

    def add(self, middleware: Middleware) -> None:
        self._middlewares.append(middleware)

    def run_pre(self, command: str, args: list[str], session: SessionState) -> bool:
        """Run all pre_command hooks. Returns False if any blocks."""
        for mw in self._middlewares:
            try:
                if not mw.pre_command(command, args, session):
                    logger.debug(
                        "command_blocked_by_middleware",
                        middleware=type(mw).__name__,
                        command=command,
                    )
                    return False
            except Exception:
                logger.debug(
                    "middleware_pre_error",
                    middleware=type(mw).__name__,
                    exc_info=True,
                )
        return True

    def run_post(self, command: str, args: list[str], session: SessionState) -> None:
        """Run all post_command hooks."""
        for mw in self._middlewares:
            try:
                mw.post_command(command, args, session)
            except Exception:
                logger.debug(
                    "middleware_post_error",
                    middleware=type(mw).__name__,
                    exc_info=True,
                )


class HookMiddleware(Middleware):
    """Fires lifecycle hooks from hooks.yaml."""

    def pre_command(self, command: str, args: list[str], session: SessionState) -> bool:
        if command != "review":
            return True
        from code_review_agent.interactive.hooks import (
            HookEvent,
            load_hooks,
            run_hooks_for_event,
        )

        hooks = load_hooks()
        if not hooks:
            return True
        results = run_hooks_for_event(
            HookEvent.PRE_REVIEW,
            {"command": command, "args": args},
            hooks,
        )
        return all(r.is_allowed for r in results)

    def post_command(self, command: str, args: list[str], session: SessionState) -> None:
        if command != "review":
            return
        from code_review_agent.interactive.hooks import (
            HookEvent,
            load_hooks,
            run_hooks_for_event,
        )

        hooks = load_hooks()
        if hooks:
            run_hooks_for_event(
                HookEvent.POST_REVIEW,
                {"command": command, "args": args},
                hooks,
            )


class UsageMiddleware(Middleware):
    """Tracks per-command token usage."""

    def post_command(self, command: str, args: list[str], session: SessionState) -> None:
        if command in ("review", "pr"):
            logger.debug(
                "command_usage",
                command=command,
                total_tokens=session.total_tokens_used,
                reviews=session.reviews_completed,
            )


class NewsCleanupMiddleware(Middleware):
    """Auto-cleanup old news articles on first command."""

    _cleaned = False

    def pre_command(self, command: str, args: list[str], session: SessionState) -> bool:
        if NewsCleanupMiddleware._cleaned:
            return True
        NewsCleanupMiddleware._cleaned = True
        try:
            from pathlib import Path

            from code_review_agent.news.storage import ArticleStore

            db = Path("~/.cra/reviews.db").expanduser()
            if db.is_file():
                store = ArticleStore(db_path=db)
                count = store.cleanup_old(days=30)
                if count > 0:
                    logger.debug("news_auto_cleanup", deleted=count)
        except Exception:  # noqa: S110
            pass
        return True


def build_default_chain() -> MiddlewareChain:
    """Build the default middleware chain."""
    return MiddlewareChain(
        [
            NewsCleanupMiddleware(),
            HookMiddleware(),
            UsageMiddleware(),
        ]
    )
