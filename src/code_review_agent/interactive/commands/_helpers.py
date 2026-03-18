"""Shared helpers for interactive commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    from code_review_agent.interactive.session import SessionState

console = Console()


def warn_if_remote_repo(session: SessionState) -> None:
    """Print a hint when the active repo is remote (not the local checkout)."""
    if session.active_repo and session.active_repo_source == "remote":
        console.print(
            f"[dim]Note: git commands operate on the local repo, not {session.active_repo}[/dim]"
        )
