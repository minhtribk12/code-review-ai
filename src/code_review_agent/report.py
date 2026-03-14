from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from code_review_agent.models import AgentStatus, Severity

if TYPE_CHECKING:
    from pathlib import Path

    from code_review_agent.models import AgentResult, ReviewReport

_SEVERITY_COLORS: dict[Severity, str] = {
    Severity.CRITICAL: "bold red",
    Severity.HIGH: "red",
    Severity.MEDIUM: "yellow",
    Severity.LOW: "green",
}

_SEVERITY_ORDER: list[Severity] = list(Severity)


def _failed_agents(report: ReviewReport) -> list[AgentResult]:
    """Return agent results with status FAILED."""
    return [r for r in report.agent_results if r.status == AgentStatus.FAILED]


def render_report_rich(report: ReviewReport) -> None:
    """Print the review report to the terminal using Rich formatting."""
    console = Console()
    failed = _failed_agents(report)

    # Header panel.
    header_lines: list[str] = [
        f"Reviewed at: {report.reviewed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"Risk level: {report.risk_level.upper()}",
    ]
    if report.pr_url is not None:
        header_lines.insert(0, f"PR: {report.pr_url}")

    totals = report.total_findings
    counts_str = " | ".join(f"{sev}: {totals[sev]}" for sev in _SEVERITY_ORDER)
    header_lines.append(f"Findings: {counts_str}")

    if failed:
        total_agents = len(report.agent_results)
        header_lines.append(
            f"WARNING: {len(failed)} of {total_agents} agents failed. Review is incomplete."
        )

    console.print(
        Panel(
            "\n".join(header_lines),
            title="Code Review Report",
            border_style=_SEVERITY_COLORS.get(report.risk_level, "white"),
        )
    )

    # Overall summary.
    console.print(f"\n[bold]Overall Summary[/bold]\n{report.overall_summary}\n")

    # Per-agent results.
    for result in report.agent_results:
        if result.status == AgentStatus.FAILED:
            console.print(f"[bold red]{result.agent_name.upper()} Agent (FAILED)[/bold red]")
            error_msg = result.error_message or "Unknown error"
            console.print(f"  [red]Error: {error_msg}[/red]\n")
        else:
            console.print(
                f"[bold]{result.agent_name.upper()} Agent[/bold] "
                f"({len(result.findings)} findings, "
                f"{result.execution_time_seconds:.1f}s)"
            )
            console.print(f"  {result.summary}\n")

    # Findings table grouped by severity.
    all_findings = [
        (finding, result.agent_name)
        for result in report.agent_results
        for finding in result.findings
    ]
    if not all_findings:
        console.print("[green]No findings -- the code looks good.[/green]")
        return

    table = Table(title="All Findings", show_lines=True)
    table.add_column("Severity", width=10)
    table.add_column("Agent", width=14)
    table.add_column("Category", width=18)
    table.add_column("Title", width=40)
    table.add_column("Location", width=25)

    sorted_findings = sorted(
        all_findings,
        key=lambda item: _SEVERITY_ORDER.index(item[0].severity),
    )

    for finding, agent_name in sorted_findings:
        color = _SEVERITY_COLORS.get(finding.severity, "white")
        location = ""
        if finding.file_path is not None:
            location = finding.file_path
            if finding.line_number is not None:
                location += f":{finding.line_number}"
        table.add_row(
            f"[{color}]{finding.severity.upper()}[/{color}]",
            agent_name,
            finding.category,
            finding.title,
            location,
        )

    console.print(table)


def render_report_markdown(report: ReviewReport) -> str:
    """Render the review report as a markdown string."""
    lines: list[str] = ["# Code Review Report", ""]
    failed = _failed_agents(report)

    if report.pr_url is not None:
        lines.append(f"**PR:** {report.pr_url}")
    lines.append(f"**Reviewed at:** {report.reviewed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append(f"**Risk level:** {report.risk_level.upper()}")

    totals = report.total_findings
    counts_str = " | ".join(f"{sev}: {totals[sev]}" for sev in _SEVERITY_ORDER)
    lines.append(f"**Findings:** {counts_str}")

    if failed:
        total_agents = len(report.agent_results)
        lines.append(
            f"**WARNING:** {len(failed)} of {total_agents} agents failed. Review is incomplete."
        )

    lines.extend(["", "## Overall Summary", "", report.overall_summary, ""])

    # Per-agent sections.
    for result in report.agent_results:
        title = result.agent_name.replace("_", " ").title()

        if result.status == AgentStatus.FAILED:
            lines.append(f"## {title} Agent (FAILED)")
            lines.append("")
            error_msg = result.error_message or "Unknown error"
            lines.append(f"**Error:** {error_msg}")
            lines.append("")
            continue

        lines.append(f"## {title} Agent")
        lines.append("")
        lines.append(
            f"*{len(result.findings)} findings | "
            f"{result.execution_time_seconds:.1f}s execution time*"
        )
        lines.append("")
        lines.append(result.summary)
        lines.append("")

        if result.findings:
            for finding in result.findings:
                severity_label = finding.severity.upper()
                lines.append(f"### [{severity_label}] {finding.title}")
                lines.append("")
                lines.append(f"**Category:** {finding.category}")
                if finding.file_path is not None:
                    location = finding.file_path
                    if finding.line_number is not None:
                        location += f":{finding.line_number}"
                    lines.append(f"**Location:** `{location}`")
                lines.append("")
                lines.append(finding.description)
                if finding.suggestion is not None:
                    lines.append("")
                    lines.append(f"**Suggestion:** {finding.suggestion}")
                lines.append("")

    return "\n".join(lines)


def save_report(report: ReviewReport, path: Path) -> None:
    """Save the review report as markdown to the specified file path."""
    content = render_report_markdown(report=report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
