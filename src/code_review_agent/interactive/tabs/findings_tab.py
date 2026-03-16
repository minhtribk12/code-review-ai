"""Findings tab: browse review findings with triage and PR posting.

Wraps the existing FindingsViewer from findings_cmd.py into a Textual
widget that renders the prompt_toolkit FormattedText as Rich markup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.containers import Vertical
from textual.widgets import Static

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from code_review_agent.interactive.session import SessionState


class FindingsTab(Vertical):
    """Findings navigator embedded in the tabbed TUI.

    Since the full findings navigator uses prompt_toolkit's Application
    (full-screen mode), this tab provides a summary view and a launch
    button to open the full navigator.
    """

    def __init__(self, session: SessionState) -> None:
        super().__init__()
        self._session = session

    def compose(self) -> ComposeResult:
        yield Static("", id="findings-summary")
        yield Static(
            " Press [bold]Enter[/bold] to open the full findings navigator.",
            id="findings-hint",
        )

    def on_mount(self) -> None:
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        label = self.query_one("#findings-summary", Static)
        report = self._session.last_review_report
        if report is None:
            label.update(" No review results yet.\n Run a review from the PR tab or CLI first.")
            return

        total = sum(len(r.findings) for r in report.agent_results)
        counts = report.total_findings
        agents = ", ".join(r.agent_name for r in report.agent_results if r.findings)
        pr_info = f" | PR: {report.pr_url}" if report.pr_url else ""

        lines = [
            f" [bold]{total} finding(s)[/bold] from last review{pr_info}",
            f" Severity: critical={counts.get('critical', 0)} "
            f"high={counts.get('high', 0)} "
            f"medium={counts.get('medium', 0)} "
            f"low={counts.get('low', 0)}",
            f" Agents: {agents}",
            f" Rounds: {report.rounds_completed}",
        ]
        if report.validation_result:
            fp = report.validation_result.false_positive_count
            lines.append(f" Validation: {fp} false positive(s) filtered")

        label.update("\n".join(lines))

    def launch_navigator(self) -> None:
        """Open the full-screen findings navigator."""
        report = self._session.last_review_report
        if report is None:
            self.notify("No review results. Run a review first.", severity="warning")
            return

        settings = self._session.effective_settings
        token: str | None = None
        if settings.github_token is not None:
            token = settings.github_token.get_secret_value()

        # Suspend Textual, run prompt_toolkit navigator, resume
        from code_review_agent.interactive.commands.findings_cmd import (
            run_findings_app,
        )

        with self.app.suspend():
            run_findings_app(report=report, github_token=token)

        self._refresh_summary()
