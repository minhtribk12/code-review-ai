"""Usage command: session usage summary with time windows and per-agent breakdown."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    from code_review_agent.interactive.session import ReviewRecord, SessionState

console = Console()

_HOUR = 3600.0
_DAY = 86400.0
_WEEK = 604800.0


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


def _window_stats(records: list[ReviewRecord]) -> tuple[int, int, int, float]:
    """Return (reviews, tokens, calls, cost) for a list of records."""
    reviews = len(records)
    tokens = sum(r.total_tokens for r in records)
    calls = sum(r.llm_calls for r in records)
    cost = sum(r.estimated_cost_usd or 0.0 for r in records)
    return reviews, tokens, calls, cost


def cmd_usage(args: list[str], session: SessionState) -> None:
    """Show session usage summary with time windows and per-agent breakdown."""
    history = session.usage_history

    # --- Summary table ---
    summary = Table(title="Session Usage", show_lines=False, expand=False)
    summary.add_column("Metric", style="bold", width=25)
    summary.add_column("Value", width=15, justify="right")

    summary.add_row("Reviews completed", str(session.reviews_completed))
    summary.add_row("Total tokens", _fmt_tokens(history.total_tokens))
    summary.add_row("Total LLM calls", str(history.total_calls))
    summary.add_row("Estimated cost", _fmt_cost(history.total_cost))
    summary.add_row("Token tier", session.display_tier)
    console.print(summary)

    if not history.records:
        return

    # --- Time window table ---
    last_hour = history.records_since(_HOUR)
    last_day = history.records_since(_DAY)
    last_week = history.records_since(_WEEK)

    window_table = Table(title="Usage by Time Window", show_lines=False, expand=False)
    window_table.add_column("Window", style="bold", width=15)
    window_table.add_column("Reviews", width=10, justify="right")
    window_table.add_column("Tokens", width=12, justify="right")
    window_table.add_column("LLM Calls", width=12, justify="right")
    window_table.add_column("Cost", width=12, justify="right")

    for label, records in [
        ("Last hour", last_hour),
        ("Last 24h", last_day),
        ("Last 7 days", last_week),
        ("All time", history.records),
    ]:
        reviews, tokens, calls, cost = _window_stats(records)
        window_table.add_row(
            label,
            str(reviews),
            _fmt_tokens(tokens),
            str(calls),
            _fmt_cost(cost),
        )

    console.print(window_table)

    # --- Per-agent table ---
    agent_totals = history.tokens_by_agent()
    if agent_totals:
        agent_table = Table(title="Tokens by Agent", show_lines=False, expand=False)
        agent_table.add_column("Agent", style="bold", width=20)
        agent_table.add_column("Tokens", width=12, justify="right")
        agent_table.add_column("% of Total", width=12, justify="right")

        total = max(sum(agent_totals.values()), 1)
        for agent_name in sorted(agent_totals):
            tokens = agent_totals[agent_name]
            pct = (tokens / total) * 100
            agent_table.add_row(
                agent_name,
                _fmt_tokens(tokens),
                f"{pct:.0f}%",
            )

        console.print(agent_table)
