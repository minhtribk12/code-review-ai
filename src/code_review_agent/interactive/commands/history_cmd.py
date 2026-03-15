"""History command: browse and query past review reports."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from code_review_agent.storage import ReviewStorage

if TYPE_CHECKING:
    from code_review_agent.interactive.session import SessionState

console = Console()


def _get_storage() -> ReviewStorage:
    """Get or create the review storage instance."""
    return ReviewStorage()


def _fmt_cost(cost: float | None) -> str:
    if cost is None or cost == 0.0:
        return "-"
    if cost < 0.01:
        return f"${cost:.4f}"
    return f"${cost:.2f}"


def _fmt_tokens(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}m"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


def cmd_history(args: list[str], session: SessionState) -> None:
    """History command router."""
    if not args:
        _history_list([], session)
        return

    sub = args[0]
    sub_args = args[1:]

    handlers = {
        "list": _history_list,
        "show": _history_show,
        "trends": _history_trends,
        "export": _history_export,
    }

    handler = handlers.get(sub)
    if handler is None:
        # If first arg is a number, treat as "history show <id>"
        try:
            int(sub)
            _history_show([sub], session)
            return
        except ValueError:
            pass

        console.print(
            f"[red]Unknown history subcommand: {sub}[/red]\n"
            "  list [--repo R] [--days N] [--limit N]  List past reviews\n"
            "  show <id>                                Show full report\n"
            "  trends [--repo R] [--days N]             Aggregated trends\n"
            "  export [--repo R] [--format json]         Export data"
        )
        return

    try:
        handler(sub_args, session)
    except Exception as exc:
        console.print(f"[red]History error: {exc}[/red]")


def _parse_flag(args: list[str], flag: str) -> str | None:
    """Extract a --flag value from args."""
    for i, arg in enumerate(args):
        if arg == flag and i + 1 < len(args):
            return args[i + 1]
    return None


def _history_list(args: list[str], session: SessionState) -> None:
    """List recent reviews."""
    repo = _parse_flag(args, "--repo") or None
    days_str = _parse_flag(args, "--days")
    limit_str = _parse_flag(args, "--limit")

    days = int(days_str) if days_str else None
    limit = int(limit_str) if limit_str else 20

    storage = _get_storage()
    reviews = storage.list_reviews(repo=repo, days=days, limit=limit)

    if not reviews:
        console.print("[dim]No reviews found.[/dim]")
        return

    table = Table(title="Review History", show_lines=False)
    table.add_column("ID", style="bold", width=5, justify="right")
    table.add_column("Date", width=12)
    table.add_column("Repo", width=25)
    table.add_column("PR", width=5, justify="right")
    table.add_column("Risk", width=10)
    table.add_column("Findings", width=10, justify="right")
    table.add_column("Tokens", width=10, justify="right")
    table.add_column("Cost", width=8, justify="right")

    for r in reviews:
        risk = str(r.get("risk_level", ""))
        risk_style = {
            "critical": "red bold",
            "high": "red",
            "medium": "yellow",
            "low": "green",
        }.get(risk, "")

        date_str = str(r.get("reviewed_at", ""))[:10]
        pr_str = str(r.get("pr_number", "")) if r.get("pr_number") else "-"

        table.add_row(
            str(r.get("id", "")),
            date_str,
            str(r.get("repo", "") or "-"),
            pr_str,
            f"[{risk_style}]{risk}[/{risk_style}]" if risk_style else risk,
            str(r.get("total_findings", 0)),
            _fmt_tokens(r.get("total_tokens", 0)),
            _fmt_cost(r.get("estimated_cost_usd")),
        )

    console.print(table)
    console.print(f"  [dim]{len(reviews)} review(s) shown[/dim]")


def _history_show(args: list[str], session: SessionState) -> None:
    """Show full details for a specific review."""
    if not args:
        console.print("[red]Usage: history show <id>[/red]")
        return

    try:
        review_id = int(args[0])
    except ValueError:
        console.print(f"[red]Invalid review ID: {args[0]}[/red]")
        return

    storage = _get_storage()
    review = storage.get_review(review_id)

    if review is None:
        console.print(f"[red]Review #{review_id} not found.[/red]")
        return

    lines = [
        f"[bold]Review #{review['id']}[/bold]",
        f"  Date:     {review['reviewed_at']}",
        f"  Repo:     {review.get('repo') or 'local'}",
        f"  PR:       #{review['pr_number']}" if review.get("pr_number") else "",
        f"  Risk:     {review['risk_level']}",
        f"  Findings: {review['total_findings']}",
        f"    Critical: {review['critical_count']}  "
        f"High: {review['high_count']}  "
        f"Medium: {review['medium_count']}  "
        f"Low: {review['low_count']}",
        f"  Tokens:   {_fmt_tokens(review['total_tokens'])}",
        f"  Cost:     {_fmt_cost(review.get('estimated_cost_usd'))}",
        f"  Agents:   {review.get('agents_used', '')}",
    ]
    if review.get("pr_url"):
        lines.append(f"  URL:      {review['pr_url']}")

    # Add summary
    summary = review.get("overall_summary", "")
    if summary:
        lines.append("")
        lines.append(f"  {summary[:500]}")

    console.print(
        Panel(
            "\n".join(line for line in lines if line),
            title="Review Detail",
            border_style="blue",
        )
    )


def _history_trends(args: list[str], session: SessionState) -> None:
    """Show aggregated trend data."""
    repo = _parse_flag(args, "--repo") or None
    days_str = _parse_flag(args, "--days")
    days = int(days_str) if days_str else 30

    storage = _get_storage()
    trends = storage.get_trends(repo=repo, days=days)

    if trends.get("review_count", 0) == 0:
        console.print(f"[dim]No reviews in the last {days} days.[/dim]")
        return

    # Summary table
    summary = Table(title=f"Trends (Last {days} Days)", show_lines=False)
    summary.add_column("Metric", style="bold", width=25)
    summary.add_column("Value", width=15, justify="right")

    summary.add_row("Reviews", str(trends["review_count"]))
    summary.add_row("Total findings", str(trends["total_findings"]))
    summary.add_row("Avg findings/review", f"{trends['avg_findings']:.1f}")
    summary.add_row("Total tokens", _fmt_tokens(int(trends["total_tokens"])))
    summary.add_row("Avg tokens/review", _fmt_tokens(int(trends["avg_tokens"])))
    summary.add_row("Total cost", _fmt_cost(trends["total_cost"]))
    summary.add_row("Avg cost/review", _fmt_cost(trends["avg_cost"]))

    console.print(summary)

    # Severity breakdown
    sev_table = Table(title="Findings by Severity", show_lines=False)
    sev_table.add_column("Severity", style="bold", width=15)
    sev_table.add_column("Count", width=10, justify="right")

    for sev, label_style in [
        ("critical", "red bold"),
        ("high", "red"),
        ("medium", "yellow"),
        ("low", "green"),
    ]:
        key = f"total_{sev}"
        count = trends.get(key, 0)
        sev_table.add_row(
            f"[{label_style}]{sev}[/{label_style}]",
            str(count),
        )

    console.print(sev_table)

    # Risk distribution
    risk_dist = trends.get("risk_distribution", {})
    if risk_dist:
        risk_table = Table(title="Risk Level Distribution", show_lines=False)
        risk_table.add_column("Risk Level", style="bold", width=15)
        risk_table.add_column("Reviews", width=10, justify="right")

        for level, count in sorted(risk_dist.items()):
            risk_table.add_row(level, str(count))

        console.print(risk_table)


def _history_export(args: list[str], session: SessionState) -> None:
    """Export review history as JSON."""
    repo = _parse_flag(args, "--repo") or None

    storage = _get_storage()
    output = storage.export_json(repo=repo)
    console.print(output)
