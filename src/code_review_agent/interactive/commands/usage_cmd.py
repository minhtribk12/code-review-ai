"""Usage command: persistent usage stats from database + current session."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    from code_review_agent.interactive.session import SessionState

console = Console()


def _fmt_tokens(count: int) -> str:
    """Format token count with human-readable suffix."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}m"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


def _fmt_cost(cost: float) -> str:
    """Format cost in USD."""
    if cost == 0.0:
        return "-"
    if cost < 0.01:
        return f"${cost:.4f}"
    return f"${cost:.2f}"


def cmd_usage(args: list[str], session: SessionState) -> None:
    """Show usage stats from database (persisted across sessions)."""
    from code_review_agent.storage import ReviewStorage

    settings = session.effective_settings
    storage = ReviewStorage(settings.history_db_path)

    # --- Current session (in-memory) ---
    session_table = Table(title="Current Session", show_lines=False, expand=False)
    session_table.add_column("Metric", style="bold", width=25)
    session_table.add_column("Value", width=15, justify="right")

    session_table.add_row("Reviews completed", str(session.reviews_completed))
    session_table.add_row("Tokens used", _fmt_tokens(session.total_tokens_used))
    session_table.add_row("Token tier", session.display_tier)
    console.print(session_table)

    # --- Persistent usage by time window (from database) ---
    windows = [
        ("Last hour", 1.0),
        ("Last 24 hours", 24.0),
        ("Last 7 days", 24.0 * 7),
        ("Last 30 days", 24.0 * 30),
        ("All time", None),
    ]

    window_table = Table(
        title="Usage History (from database)",
        show_lines=False,
        expand=False,
    )
    window_table.add_column("Window", style="bold", width=15)
    window_table.add_column("Reviews", width=10, justify="right")
    window_table.add_column("Tokens", width=12, justify="right")
    window_table.add_column("LLM Calls", width=12, justify="right")
    window_table.add_column("Cost", width=12, justify="right")

    for label, hours in windows:
        stats = storage.get_usage_stats(hours=hours)
        window_table.add_row(
            label,
            str(stats["review_count"]),
            _fmt_tokens(stats["total_tokens"]),
            str(stats["llm_calls"]),
            _fmt_cost(stats["estimated_cost_usd"]),
        )

    console.print(window_table)

    # --- Per-agent breakdown (all time from database) ---
    agent_stats = storage.get_usage_by_agent()
    if agent_stats:
        agent_table = Table(
            title="Agent Usage (all time)",
            show_lines=False,
            expand=False,
        )
        agent_table.add_column("Agent", style="bold", width=20)
        agent_table.add_column("Runs", width=10, justify="right")
        agent_table.add_column("Findings", width=12, justify="right")

        for row in agent_stats:
            agent_table.add_row(
                row["agent_name"],
                str(row["runs"]),
                str(row["total_findings"]),
            )

        console.print(agent_table)
