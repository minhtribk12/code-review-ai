"""Interactive findings navigator with triage actions and PR posting.

Full-screen TUI launched via the ``findings`` REPL command. Users can
browse, filter, sort, triage findings, and post them as inline PR
review comments.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from prompt_toolkit import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from pydantic import BaseModel
from rich.console import Console

from code_review_agent.github_client import (
    GitHubAuthError,
    parse_pr_reference,
    submit_pr_review_with_comments,
)
from code_review_agent.models import (
    Confidence,
    ReviewReport,
    Severity,
)

if TYPE_CHECKING:
    from code_review_agent.interactive.session import SessionState

logger = structlog.get_logger(__name__)

# Severity sort order: critical first
_SEVERITY_ORDER: list[Severity] = [
    Severity.CRITICAL,
    Severity.HIGH,
    Severity.MEDIUM,
    Severity.LOW,
]

_SEVERITY_STYLES: dict[str, str] = {
    "critical": "bold red",
    "high": "red",
    "medium": "yellow",
    "low": "green",
}


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class FindingRow(BaseModel):
    """Flattened finding for display in the navigator."""

    model_config = {"frozen": True}

    index: int
    severity: Severity
    agent_name: str
    category: str
    title: str
    description: str
    file_path: str | None = None
    line_number: int | None = None
    suggestion: str | None = None
    confidence: Confidence = Confidence.MEDIUM


class TriageAction(StrEnum):
    """In-memory triage annotation for a finding."""

    NONE = "none"
    FALSE_POSITIVE = "false_positive"
    IGNORED = "ignored"


class _ViewerMode(StrEnum):
    NAVIGATE = "navigate"
    FILTER = "filter"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _flatten_findings(report: ReviewReport) -> list[FindingRow]:
    """Extract all findings from a report into a flat list of FindingRow."""
    rows: list[FindingRow] = []
    idx = 0
    for result in report.agent_results:
        for finding in result.findings:
            rows.append(
                FindingRow(
                    index=idx,
                    severity=finding.severity,
                    agent_name=result.agent_name,
                    category=finding.category,
                    title=finding.title,
                    description=finding.description,
                    file_path=finding.file_path,
                    line_number=finding.line_number,
                    suggestion=finding.suggestion,
                    confidence=finding.confidence,
                )
            )
            idx += 1
    return rows


def _format_severity(sev: str) -> str:
    """Format severity as a fixed-width uppercase label."""
    return sev.upper()[:4].ljust(4)


def _format_location(file_path: str | None, line_number: int | None) -> str:
    """Format file:line as a truncated string."""
    if file_path is None:
        return ""
    loc = file_path
    if line_number is not None:
        loc = f"{file_path}:{line_number}"
    if len(loc) > 30:
        loc = "..." + loc[-27:]
    return loc


# ---------------------------------------------------------------------------
# FindingsViewer state machine
# ---------------------------------------------------------------------------


class FindingsViewer:
    """State machine driving the findings navigator TUI."""

    def __init__(
        self,
        report: ReviewReport,
        *,
        github_token: str | None = None,
    ) -> None:
        self.report = report
        self.github_token = github_token
        self.all_rows: list[FindingRow] = _flatten_findings(report)
        self.visible_rows: list[FindingRow] = list(self.all_rows)
        self.cursor: int = 0
        self.is_detail_open: bool = False
        self.mode: _ViewerMode = _ViewerMode.NAVIGATE
        self.status_message: str = ""

        # Triage
        self.triage: dict[int, TriageAction] = {}
        self.staged_for_pr: set[int] = set()

        # Sort
        self.sort_columns: list[str] = [
            "severity",
            "agent_name",
            "file_path",
            "title",
        ]
        self.sort_index: int = 0
        self.is_sort_reversed: bool = False

        # Filter
        self.filter_severity: set[Severity] = set(Severity)
        self.filter_agents: set[str] = {r.agent_name for r in report.agent_results}
        self.filter_cursor: int = 0
        self.filter_options: list[tuple[str, str, bool]] = []

        # PR posting tracking
        self.comments_posted: int = 0

    # -- Navigation --

    def move_up(self) -> None:
        self.status_message = ""
        if self.cursor > 0:
            self.cursor -= 1

    def move_down(self) -> None:
        self.status_message = ""
        if self.cursor < len(self.visible_rows) - 1:
            self.cursor += 1

    def toggle_detail(self) -> None:
        self.status_message = ""
        self.is_detail_open = not self.is_detail_open

    # -- Sort --

    def cycle_sort(self) -> None:
        self.status_message = ""
        self.sort_index = (self.sort_index + 1) % len(self.sort_columns)
        self._apply_sort()
        self.cursor = 0

    def _apply_sort(self) -> None:
        col = self.sort_columns[self.sort_index]

        def _sort_by_severity(r: FindingRow) -> int:
            return _SEVERITY_ORDER.index(r.severity)

        def _sort_by_agent(r: FindingRow) -> str:
            return r.agent_name

        def _sort_by_file(r: FindingRow) -> str:
            return r.file_path or "zzz"

        def _sort_by_title(r: FindingRow) -> str:
            return r.title.lower()

        sort_fns: dict[str, Any] = {
            "severity": _sort_by_severity,
            "agent_name": _sort_by_agent,
            "file_path": _sort_by_file,
            "title": _sort_by_title,
        }
        key_fn = sort_fns.get(col, _sort_by_severity)
        self.visible_rows.sort(key=key_fn, reverse=self.is_sort_reversed)

    # -- Filter --

    def open_filter(self) -> None:
        self.status_message = ""
        self.mode = _ViewerMode.FILTER
        self.filter_cursor = 0
        self._build_filter_options()

    def _build_filter_options(self) -> None:
        self.filter_options = []
        for sev in Severity:
            self.filter_options.append(
                (
                    f"[Severity] {sev.value}",
                    f"sev:{sev.value}",
                    sev in self.filter_severity,
                )
            )
        all_agents = sorted({r.agent_name for r in self.report.agent_results})
        for agent in all_agents:
            self.filter_options.append(
                (
                    f"[Agent] {agent}",
                    f"agent:{agent}",
                    agent in self.filter_agents,
                )
            )

    def filter_move_up(self) -> None:
        if self.filter_cursor > 0:
            self.filter_cursor -= 1

    def filter_move_down(self) -> None:
        if self.filter_cursor < len(self.filter_options) - 1:
            self.filter_cursor += 1

    def filter_toggle(self) -> None:
        if not self.filter_options:
            return
        label, key, checked = self.filter_options[self.filter_cursor]
        self.filter_options[self.filter_cursor] = (label, key, not checked)

    def filter_confirm(self) -> None:
        self.filter_severity = set()
        self.filter_agents = set()
        for _label, key, checked in self.filter_options:
            if not checked:
                continue
            if key.startswith("sev:"):
                self.filter_severity.add(Severity(key[4:]))
            elif key.startswith("agent:"):
                self.filter_agents.add(key[6:])
        self._apply_filters()
        self.mode = _ViewerMode.NAVIGATE

    def cancel_filter(self) -> None:
        self.mode = _ViewerMode.NAVIGATE

    def _apply_filters(self) -> None:
        self.visible_rows = [
            r
            for r in self.all_rows
            if r.severity in self.filter_severity and r.agent_name in self.filter_agents
        ]
        self._apply_sort()
        self.cursor = min(self.cursor, max(0, len(self.visible_rows) - 1))

    # -- Triage --

    def mark_false_positive(self) -> None:
        if not self.visible_rows:
            return
        row = self.visible_rows[self.cursor]
        current = self.triage.get(row.index, TriageAction.NONE)
        if current == TriageAction.FALSE_POSITIVE:
            self.triage.pop(row.index, None)
            self.status_message = f"Unmarked: {row.title}"
        else:
            self.triage[row.index] = TriageAction.FALSE_POSITIVE
            self.status_message = f"Marked as false positive: {row.title}"

    def mark_ignored(self) -> None:
        if not self.visible_rows:
            return
        row = self.visible_rows[self.cursor]
        current = self.triage.get(row.index, TriageAction.NONE)
        if current == TriageAction.IGNORED:
            self.triage.pop(row.index, None)
            self.status_message = f"Unignored: {row.title}"
        else:
            self.triage[row.index] = TriageAction.IGNORED
            self.status_message = f"Ignored: {row.title}"

    # -- PR staging --

    def toggle_stage_for_pr(self) -> None:
        if not self.visible_rows:
            return
        row = self.visible_rows[self.cursor]
        if row.index in self.staged_for_pr:
            self.staged_for_pr.discard(row.index)
            self.status_message = f"Unstaged: {row.title}"
        else:
            self.staged_for_pr.add(row.index)
            self.status_message = f"Staged for PR: {row.title}"

    def submit_to_pr(self) -> None:
        """Post staged findings as inline PR review comments."""
        if not self.report.pr_url:
            self.status_message = "! Not a PR review (local diff)"
            return
        if not self.github_token:
            self.status_message = "! GITHUB_TOKEN required for PR posting"
            return
        if not self.staged_for_pr:
            self.status_message = "! No findings staged (use 'p' to stage)"
            return

        try:
            owner, repo, pr_number = parse_pr_reference(self.report.pr_url)
        except ValueError as exc:
            self.status_message = f"! Invalid PR URL: {exc}"
            return

        # Split staged findings: inline (have location) vs general (no location)
        inline_comments: list[dict[str, Any]] = []
        general_findings: list[str] = []
        for row in self.all_rows:
            if row.index not in self.staged_for_pr:
                continue
            comment_body = (
                f"**{row.severity.value.upper()}** ({row.agent_name}): "
                f"{row.title}\n\n{row.description}"
            )
            if row.suggestion:
                comment_body += f"\n\n**Suggestion:** {row.suggestion}"

            if row.file_path is not None and row.line_number is not None:
                inline_comments.append(
                    {
                        "path": row.file_path,
                        "line": row.line_number,
                        "body": comment_body,
                    }
                )
            else:
                general_findings.append(comment_body)

        # Build review body: include general findings without location
        body_parts = ["Code review findings from automated analysis."]
        if general_findings:
            body_parts.append("")
            body_parts.append("---")
            body_parts.append("")
            for gf in general_findings:
                body_parts.append(gf)
                body_parts.append("")
        review_body = "\n".join(body_parts)

        try:
            result = submit_pr_review_with_comments(
                owner=owner,
                repo=repo,
                token=self.github_token,
                pr_number=pr_number,
                body=review_body,
                comments=inline_comments,
            )
            posted_inline = result.get("comments_posted", len(inline_comments))
            total_posted = posted_inline + len(general_findings)
            self.comments_posted += total_posted
            self.staged_for_pr.clear()
            self.status_message = (
                f"Posted {total_posted} findings to PR #{pr_number}"
                f" ({posted_inline} inline, {len(general_findings)} in body)"
            )
        except GitHubAuthError:
            self.status_message = "! Permission denied (check token scope)"
        except httpx.HTTPStatusError as exc:
            self.status_message = f"! GitHub API error: {exc.response.status_code}"
        except Exception as exc:
            logger.exception("pr review posting failed")
            self.status_message = f"! Error posting review: {exc}"

    # -- Rendering --

    def render(self) -> FormattedText:
        if self.mode == _ViewerMode.FILTER:
            return self._render_filter()
        return self._render_navigate()

    def _render_navigate(self) -> FormattedText:
        lines: list[tuple[str, str]] = []

        # Header
        lines.append(("bold", " Findings Navigator"))
        lines.append(("", "  ("))
        lines.append(("cyan", "Up/Down"))
        lines.append(("", " nav, "))
        lines.append(("cyan", "Enter"))
        lines.append(("", " detail, "))
        lines.append(("cyan", "f"))
        lines.append(("", "ilter, "))
        lines.append(("cyan", "s"))
        lines.append(("", "ort, "))
        lines.append(("cyan", "m"))
        lines.append(("", "ark FP, "))
        lines.append(("cyan", "i"))
        lines.append(("", "gnore, "))
        lines.append(("cyan", "p"))
        lines.append(("", "/"))
        lines.append(("cyan", "P"))
        lines.append(("", " PR, "))
        lines.append(("cyan", "q"))
        lines.append(("", "uit)\n"))

        # Status bar
        total = len(self.visible_rows)
        all_total = len(self.all_rows)
        sort_col = self.sort_columns[self.sort_index]
        staged = len(self.staged_for_pr)

        status_parts: list[str] = [f" {total}/{all_total} findings"]
        if total < all_total:
            status_parts.append("(filtered)")
        status_parts.append(f"| sort: {sort_col}")
        if staged:
            status_parts.append(f"| {staged} staged for PR")
        lines.append(("dim", " ".join(status_parts) + "\n"))

        # Status message
        if self.status_message:
            style = "yellow bold" if self.status_message.startswith("!") else "green"
            lines.append((style, f" {self.status_message}\n"))

        lines.append(("", "\n"))

        if not self.visible_rows:
            lines.append(("dim", "  No findings match current filters.\n"))
            return FormattedText(lines)

        # Determine viewport
        detail_lines = 12 if self.is_detail_open else 0
        viewport_size = max(10, 30 - detail_lines)
        visible_start = max(0, self.cursor - viewport_size // 2)
        visible_end = min(len(self.visible_rows), visible_start + viewport_size)

        # Table header
        lines.append(("bold dim", "   Sev  Agent        File:Line                      Title\n"))
        lines.append(("dim", "   " + "-" * 75 + "\n"))

        # Table rows
        for i in range(visible_start, visible_end):
            row = self.visible_rows[i]
            is_selected = i == self.cursor
            triage_action = self.triage.get(row.index, TriageAction.NONE)
            is_staged = row.index in self.staged_for_pr

            # Row prefix
            if is_selected:
                lines.append(("reverse bold", " > "))
            else:
                lines.append(("", "   "))

            # Determine base style for triaged rows
            if triage_action == TriageAction.FALSE_POSITIVE:
                base_style = "strike dim"
            elif triage_action == TriageAction.IGNORED:
                base_style = "dim"
            elif is_selected:
                base_style = "bold"
            else:
                base_style = ""

            # Severity
            sev_label = _format_severity(row.severity.value)
            sev_style = _SEVERITY_STYLES.get(row.severity.value, "")
            if triage_action != TriageAction.NONE:
                sev_style = base_style
            lines.append((sev_style, f"{sev_label} "))

            # Agent
            lines.append((base_style, f"{row.agent_name:<12} "))

            # Location
            loc = _format_location(row.file_path, row.line_number)
            lines.append((base_style, f"{loc:<30} "))

            # Title (truncated)
            title = row.title[:40]
            lines.append((base_style, title))

            # Triage / staged indicator
            if triage_action == TriageAction.FALSE_POSITIVE:
                lines.append(("dim", " [FP]"))
            elif triage_action == TriageAction.IGNORED:
                lines.append(("dim", " [IGN]"))
            elif is_staged:
                lines.append(("cyan", " [PR]"))

            lines.append(("", "\n"))

        # Detail panel
        if self.is_detail_open and self.visible_rows:
            lines.append(("", "\n"))
            lines.append(("dim", "   " + "=" * 75 + "\n"))
            row = self.visible_rows[self.cursor]

            lines.append(("bold", f"   {row.title}\n"))
            lines.append(("", "\n"))

            lines.append(("dim", "   Agent: "))
            lines.append(("", f"{row.agent_name}"))
            lines.append(("dim", "  |  Severity: "))
            sev_style = _SEVERITY_STYLES.get(row.severity.value, "")
            lines.append((sev_style, row.severity.value.upper()))
            lines.append(("dim", "  |  Confidence: "))
            lines.append(("", f"{row.confidence.value}\n"))

            if row.file_path:
                loc = row.file_path
                if row.line_number is not None:
                    loc = f"{row.file_path}:{row.line_number}"
                lines.append(("dim", "   File: "))
                lines.append(("cyan", f"{loc}\n"))

            lines.append(("", "\n"))
            lines.append(("dim", "   Description:\n"))
            for desc_line in row.description.split("\n"):
                lines.append(("", f"     {desc_line}\n"))

            if row.suggestion:
                lines.append(("", "\n"))
                lines.append(("dim", "   Suggestion:\n"))
                for sug_line in row.suggestion.split("\n"):
                    lines.append(("green", f"     {sug_line}\n"))

        return FormattedText(lines)

    def _render_filter(self) -> FormattedText:
        lines: list[tuple[str, str]] = []

        lines.append(("bold", " Filter Findings\n"))
        lines.append(("dim", " (Up/Down navigate, Enter/Space toggle, Tab confirm, Esc cancel)\n"))
        lines.append(("", "\n"))

        for i, (label, _key, checked) in enumerate(self.filter_options):
            is_selected = i == self.filter_cursor
            checkbox = "[x]" if checked else "[ ]"

            if is_selected:
                lines.append(("reverse bold", " > "))
            else:
                lines.append(("", "   "))

            style = "bold" if is_selected else ""
            lines.append((style, f"{checkbox} {label}\n"))

        lines.append(("", "\n"))
        checked_count = sum(1 for _, _, c in self.filter_options if c)
        total = len(self.filter_options)
        lines.append(("dim", f" {checked_count}/{total} selected\n"))

        return FormattedText(lines)


# ---------------------------------------------------------------------------
# Command entry point
# ---------------------------------------------------------------------------


def cmd_findings(args: list[str], session: SessionState) -> None:
    """Launch the interactive findings navigator."""
    console = Console()
    report: ReviewReport | None = None

    # Load report: from arg (review ID) or last review
    if args and args[0].isdigit():
        review_id = int(args[0])
        try:
            from code_review_agent.storage import ReviewStorage

            storage = ReviewStorage(session.effective_settings.history_db_path)
            review_dict = storage.get_review(review_id)
            if review_dict is None:
                console.print(f"[red]Review #{review_id} not found.[/red]")
                return
            report = ReviewReport.model_validate_json(
                review_dict["report_json"],
            )
        except Exception as exc:
            console.print(f"[red]Failed to load review #{review_id}: {exc}[/red]")
            return
    else:
        report = session.last_review_report

    if report is None:
        console.print(
            "[yellow]No review available. "
            "Run 'review' first or specify a review ID: "
            "findings <review_id>[/yellow]"
        )
        return

    findings = _flatten_findings(report)
    if not findings:
        console.print("[dim]No findings to display.[/dim]")
        return

    # Resolve GitHub token for PR posting
    settings = session.effective_settings
    token: str | None = None
    if settings.github_token is not None:
        token = settings.github_token.get_secret_value()

    viewer = FindingsViewer(report, github_token=token)

    # -- Key bindings --
    kb = KeyBindings()

    @kb.add("up")
    def on_up(_event: KeyPressEvent) -> None:
        if viewer.mode == _ViewerMode.NAVIGATE:
            viewer.move_up()
        elif viewer.mode == _ViewerMode.FILTER:
            viewer.filter_move_up()

    @kb.add("down")
    def on_down(_event: KeyPressEvent) -> None:
        if viewer.mode == _ViewerMode.NAVIGATE:
            viewer.move_down()
        elif viewer.mode == _ViewerMode.FILTER:
            viewer.filter_move_down()

    @kb.add("enter")
    def on_enter(_event: KeyPressEvent) -> None:
        if viewer.mode == _ViewerMode.NAVIGATE:
            viewer.toggle_detail()
        elif viewer.mode == _ViewerMode.FILTER:
            viewer.filter_toggle()

    @kb.add("space")
    def on_space(_event: KeyPressEvent) -> None:
        if viewer.mode == _ViewerMode.NAVIGATE:
            viewer.toggle_detail()
        elif viewer.mode == _ViewerMode.FILTER:
            viewer.filter_toggle()

    @kb.add("f")
    def on_filter(_event: KeyPressEvent) -> None:
        if viewer.mode == _ViewerMode.NAVIGATE:
            viewer.open_filter()

    @kb.add("s")
    def on_sort(_event: KeyPressEvent) -> None:
        if viewer.mode == _ViewerMode.NAVIGATE:
            viewer.cycle_sort()

    @kb.add("m")
    def on_mark_fp(_event: KeyPressEvent) -> None:
        if viewer.mode == _ViewerMode.NAVIGATE:
            viewer.mark_false_positive()

    @kb.add("i")
    def on_ignore(_event: KeyPressEvent) -> None:
        if viewer.mode == _ViewerMode.NAVIGATE:
            viewer.mark_ignored()

    @kb.add("p")
    def on_stage_pr(_event: KeyPressEvent) -> None:
        if viewer.mode == _ViewerMode.NAVIGATE:
            viewer.toggle_stage_for_pr()

    @kb.add("P")  # Shift+P
    def on_submit_pr(_event: KeyPressEvent) -> None:
        if viewer.mode == _ViewerMode.NAVIGATE:
            viewer.submit_to_pr()

    @kb.add("tab")
    def on_tab(_event: KeyPressEvent) -> None:
        if viewer.mode == _ViewerMode.FILTER:
            viewer.filter_confirm()

    @kb.add("escape")
    def on_escape(event: KeyPressEvent) -> None:
        if viewer.mode == _ViewerMode.FILTER:
            viewer.cancel_filter()
        else:
            event.app.exit()

    @kb.add("q")
    def on_quit(event: KeyPressEvent) -> None:
        if viewer.mode == _ViewerMode.NAVIGATE:
            event.app.exit()

    # -- Layout and run --
    control = FormattedTextControl(viewer.render)
    window = Window(content=control, wrap_lines=True)
    layout = Layout(HSplit([window]))

    app: Application[None] = Application(
        layout=layout,
        key_bindings=kb,
        full_screen=True,
        refresh_interval=0.1,
    )
    app.run()

    # -- Post-TUI summary --
    fp_count = sum(1 for v in viewer.triage.values() if v == TriageAction.FALSE_POSITIVE)
    ign_count = sum(1 for v in viewer.triage.values() if v == TriageAction.IGNORED)

    if fp_count or ign_count or viewer.comments_posted:
        console.print()
        if fp_count:
            console.print(f"  {fp_count} finding(s) marked as false positive")
        if ign_count:
            console.print(f"  {ign_count} finding(s) ignored")
        if viewer.comments_posted:
            console.print(f"  {viewer.comments_posted} comment(s) posted to PR")
        console.print()
