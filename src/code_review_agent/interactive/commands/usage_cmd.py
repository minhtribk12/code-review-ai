"""Usage commands: usage summary and detail."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel

if TYPE_CHECKING:
    from code_review_agent.interactive.session import SessionState

console = Console()


def cmd_usage(args: list[str], session: SessionState) -> None:
    """Show session usage summary."""
    lines = [
        f"Reviews completed:  {session.reviews_completed}",
    ]

    console.print(Panel("\n".join(lines), title="Session Usage", border_style="blue"))
