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
    delete_review_comments,
    get_review_comments,
    parse_pr_reference,
    submit_pr_review_with_comments,
)
from code_review_agent.models import (
    Confidence,
    ReviewReport,
    Severity,
)
from code_review_agent.theme import SEVERITY_STYLES as _SEVERITY_STYLES
from code_review_agent.theme import theme

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
    repo: str | None = None
    pr_number: int | None = None


class TriageAction(StrEnum):
    """In-memory triage annotation for a finding."""

    NONE = "none"
    FALSE_POSITIVE = "false_positive"
    IGNORED = "ignored"


class _ViewerMode(StrEnum):
    NAVIGATE = "navigate"
    FILTER = "filter"
    COLUMNS = "columns"
    HELP = "help"


# All available columns with display config
_ALL_COLUMNS: list[tuple[str, str, int]] = [
    # (key, header_label, width)
    ("severity", "Sev", 5),
    ("agent_name", "Agent", 13),
    ("file_line", "File:Line", 27),
    ("title", "Title", 21),
    ("triage", "Triage", 8),
    ("pr_status", "PR", 9),
    ("repo", "Repo", 16),
    ("pr_number", "PR#", 5),
    ("confidence", "Conf", 7),
    ("category", "Category", 14),
]

_DEFAULT_VISIBLE: list[str] = [
    "severity",
    "agent_name",
    "file_line",
    "title",
    "triage",
    "pr_status",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _flatten_findings(report: ReviewReport) -> list[FindingRow]:
    """Extract all findings from a report into a flat list of FindingRow."""
    # Extract repo/PR from pr_url if available
    repo_name: str | None = None
    pr_num: int | None = None
    if report.pr_url:
        try:
            owner, repo, pr_num = parse_pr_reference(report.pr_url)
            repo_name = f"{owner}/{repo}"
        except ValueError:
            pass

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
                    repo=repo_name,
                    pr_number=pr_num,
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

        # Filter (extended with triage/pr_status/repo/pr_number)
        self.filter_severity: set[Severity] = set(Severity)
        self.filter_agents: set[str] = {r.agent_name for r in report.agent_results}
        self.filter_triage: set[str] = {"none", "false_positive", "ignored"}
        self.filter_pr_status: set[str] = {"none", "staged", "posted"}
        self.filter_repos: set[str] = {r.repo for r in self.all_rows if r.repo}
        self.filter_pr_numbers: set[int] = {
            r.pr_number for r in self.all_rows if r.pr_number is not None
        }
        self.filter_cursor: int = 0
        self.filter_options: list[tuple[str, str, bool]] = []

        # Column configuration
        self.visible_columns: list[str] = list(_DEFAULT_VISIBLE)
        self.column_cursor: int = 0
        self.column_options: list[tuple[str, str, bool]] = []

        # PR posting tracking
        self.comments_posted: int = 0
        self.posted_indices: set[int] = set()
        self.last_review_id: int | None = None
        self.last_comment_ids: list[int] = []
        self.comments_deleted: int = 0

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

        # Severity (always shown)
        for sev in Severity:
            self.filter_options.append(
                (
                    f"[Severity] {sev.value}",
                    f"sev:{sev.value}",
                    sev in self.filter_severity,
                )
            )

        # Agent (always shown)
        all_agents = sorted({r.agent_name for r in self.report.agent_results})
        for agent in all_agents:
            self.filter_options.append(
                (
                    f"[Agent] {agent}",
                    f"agent:{agent}",
                    agent in self.filter_agents,
                )
            )

        # Triage (only if any findings are triaged)
        if self.triage:
            for label, key in [
                ("None (untriaged)", "triage:none"),
                ("False Positive", "triage:false_positive"),
                ("Ignored", "triage:ignored"),
            ]:
                self.filter_options.append(
                    (f"[Triage] {label}", key, key[7:] in self.filter_triage)
                )

        # PR Status (only if any findings are staged or posted)
        if self.staged_for_pr or self.posted_indices:
            for label, key in [
                ("Not staged", "prstatus:none"),
                ("Staged", "prstatus:staged"),
                ("Posted", "prstatus:posted"),
            ]:
                self.filter_options.append(
                    (f"[PR Status] {label}", key, key[9:] in self.filter_pr_status)
                )

        # Repo (only if multiple repos)
        all_repos = sorted({r.repo for r in self.all_rows if r.repo})
        if len(all_repos) > 1:
            for repo in all_repos:
                self.filter_options.append(
                    (f"[Repo] {repo}", f"repo:{repo}", repo in self.filter_repos)
                )

        # PR number (only if multiple PRs)
        all_prs = sorted({r.pr_number for r in self.all_rows if r.pr_number is not None})
        if len(all_prs) > 1:
            for pr_num in all_prs:
                self.filter_options.append(
                    (
                        f"[PR] #{pr_num}",
                        f"prnum:{pr_num}",
                        pr_num in self.filter_pr_numbers,
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
        self.filter_triage = set()
        self.filter_pr_status = set()
        self.filter_repos = set()
        self.filter_pr_numbers = set()
        for _label, key, checked in self.filter_options:
            if not checked:
                continue
            if key.startswith("sev:"):
                self.filter_severity.add(Severity(key[4:]))
            elif key.startswith("agent:"):
                self.filter_agents.add(key[6:])
            elif key.startswith("triage:"):
                self.filter_triage.add(key[7:])
            elif key.startswith("prstatus:"):
                self.filter_pr_status.add(key[9:])
            elif key.startswith("repo:"):
                self.filter_repos.add(key[5:])
            elif key.startswith("prnum:"):
                self.filter_pr_numbers.add(int(key[6:]))

        # If no triage/prstatus/repo/prnum options were in the filter,
        # keep the defaults (show all)
        if not any(k.startswith("triage:") for _, k, _ in self.filter_options):
            self.filter_triage = {"none", "false_positive", "ignored"}
        if not any(k.startswith("prstatus:") for _, k, _ in self.filter_options):
            self.filter_pr_status = {"none", "staged", "posted"}
        if not any(k.startswith("repo:") for _, k, _ in self.filter_options):
            self.filter_repos = {r.repo for r in self.all_rows if r.repo}
        if not any(k.startswith("prnum:") for _, k, _ in self.filter_options):
            self.filter_pr_numbers = {
                r.pr_number for r in self.all_rows if r.pr_number is not None
            }

        self._apply_filters()
        self.mode = _ViewerMode.NAVIGATE

    def cancel_filter(self) -> None:
        self.mode = _ViewerMode.NAVIGATE

    def _get_finding_triage_key(self, index: int) -> str:
        """Return the triage filter key for a finding index."""
        action = self.triage.get(index, TriageAction.NONE)
        return action.value

    def _get_finding_pr_status_key(self, index: int) -> str:
        """Return the PR status filter key for a finding index."""
        if index in self.posted_indices:
            return "posted"
        if index in self.staged_for_pr:
            return "staged"
        return "none"

    def _apply_filters(self) -> None:
        filtered: list[FindingRow] = []
        for r in self.all_rows:
            if r.severity not in self.filter_severity:
                continue
            if r.agent_name not in self.filter_agents:
                continue
            if self._get_finding_triage_key(r.index) not in self.filter_triage:
                continue
            if self._get_finding_pr_status_key(r.index) not in self.filter_pr_status:
                continue
            if r.repo and r.repo not in self.filter_repos:
                continue
            if r.pr_number is not None and r.pr_number not in self.filter_pr_numbers:
                continue
            filtered.append(r)
        self.visible_rows = filtered
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
        if self.last_comment_ids:
            self.status_message = (
                "! Comments already posted. Press 'D' to delete first, then re-post."
            )
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
            self.posted_indices.update(self.staged_for_pr)
            self.staged_for_pr.clear()

            # Track posted review for deletion
            review_id = result.get("id")
            if review_id is not None:
                self.last_review_id = review_id
                self._fetch_comment_ids(owner, repo, pr_number, review_id)

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

    def _fetch_comment_ids(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        review_id: int,
    ) -> None:
        """Fetch and store comment IDs from the last posted review."""
        if self.github_token is None:
            return
        try:
            comments = get_review_comments(
                owner=owner,
                repo=repo,
                token=self.github_token,
                pr_number=pr_number,
                review_id=review_id,
            )
            self.last_comment_ids = [c["id"] for c in comments if "id" in c]
        except Exception:
            logger.debug("could not fetch review comment IDs", exc_info=True)

    def delete_posted_comments(self) -> None:
        """Delete previously posted PR review comments."""
        if not self.last_comment_ids:
            self.status_message = "! No posted comments to delete"
            return
        if not self.report.pr_url:
            self.status_message = "! Not a PR review (local diff)"
            return
        if not self.github_token:
            self.status_message = "! GITHUB_TOKEN required"
            return

        try:
            owner, repo, _pr_number = parse_pr_reference(self.report.pr_url)
        except ValueError as exc:
            self.status_message = f"! Invalid PR URL: {exc}"
            return

        try:
            deleted = delete_review_comments(
                owner=owner,
                repo=repo,
                token=self.github_token,
                comment_ids=self.last_comment_ids,
            )
            self.comments_deleted += deleted
            self.last_comment_ids.clear()
            self.last_review_id = None
            self.posted_indices.clear()
            self.status_message = (
                f"Deleted {deleted} comment(s). Stage findings and press 'P' to re-post."
            )
        except GitHubAuthError:
            self.status_message = "! Permission denied (check token scope)"
        except Exception as exc:
            logger.exception("comment deletion failed")
            self.status_message = f"! Error deleting comments: {exc}"

    # -- Help --

    def show_help(self) -> None:
        self.mode = _ViewerMode.HELP

    def dismiss_help(self) -> None:
        self.mode = _ViewerMode.NAVIGATE

    # -- Column config --

    def open_columns(self) -> None:
        self.status_message = ""
        self.mode = _ViewerMode.COLUMNS
        self.column_cursor = 0
        self._build_column_options()

    def _build_column_options(self) -> None:
        self.column_options = [
            (label, key, key in self.visible_columns) for key, label, _width in _ALL_COLUMNS
        ]

    def column_move_up(self) -> None:
        if self.column_cursor > 0:
            self.column_cursor -= 1

    def column_move_down(self) -> None:
        if self.column_cursor < len(self.column_options) - 1:
            self.column_cursor += 1

    def column_toggle(self) -> None:
        if not self.column_options:
            return
        label, key, checked = self.column_options[self.column_cursor]
        self.column_options[self.column_cursor] = (label, key, not checked)

    def column_confirm(self) -> None:
        self.visible_columns = [key for _label, key, checked in self.column_options if checked]
        self.mode = _ViewerMode.NAVIGATE

    def cancel_columns(self) -> None:
        self.mode = _ViewerMode.NAVIGATE

    # -- Rendering --

    def render(self) -> FormattedText:
        if self.mode == _ViewerMode.HELP:
            return self._render_help()
        if self.mode == _ViewerMode.FILTER:
            return self._render_filter()
        if self.mode == _ViewerMode.COLUMNS:
            return self._render_columns()
        return self._render_navigate()

    def _pr_status_label(self, index: int) -> tuple[str, str]:
        """Return (style, label) for a finding's PR posting status."""
        if index in self.posted_indices:
            return (theme.success, "[POSTED]")
        if index in self.staged_for_pr:
            return (theme.accent, "[STAGED]")
        return ("", "")

    def _triage_label(self, index: int) -> tuple[str, str]:
        """Return (style, label) for a finding's triage status."""
        action = self.triage.get(index, TriageAction.NONE)
        if action == TriageAction.FALSE_POSITIVE:
            return (theme.muted, "[FP]")
        if action == TriageAction.IGNORED:
            return (theme.muted, "[IGN]")
        return ("", "")

    def _render_cell(
        self,
        row: FindingRow,
        col_key: str,
        width: int,
        base_style: str,
        triage_action: TriageAction,
    ) -> tuple[str, str]:
        """Render a single table cell, returning (style, text)."""
        if col_key == "severity":
            label = _format_severity(row.severity.value)
            style = _SEVERITY_STYLES.get(row.severity.value, "")
            if triage_action != TriageAction.NONE:
                style = base_style
            return (style, f"{label:<{width}}")

        if col_key == "agent_name":
            return (base_style, f"{row.agent_name:<{width}}")

        if col_key == "file_line":
            loc = _format_location(row.file_path, row.line_number)
            return (base_style, f"{loc:<{width}}")

        if col_key == "title":
            title = row.title[: width - 1]
            return (base_style, f"{title:<{width}}")

        if col_key == "triage":
            tri_style, tri_label = self._triage_label(row.index)
            return (tri_style, f"{tri_label:<{width}}")

        if col_key == "pr_status":
            pr_style, pr_label = self._pr_status_label(row.index)
            return (pr_style, f"{pr_label:<{width}}")

        if col_key == "repo":
            repo = row.repo or ""
            return (base_style, f"{repo:<{width}}")

        if col_key == "pr_number":
            pr_str = f"#{row.pr_number}" if row.pr_number is not None else ""
            return (base_style, f"{pr_str:<{width}}")

        if col_key == "confidence":
            return (base_style, f"{row.confidence.value:<{width}}")

        if col_key == "category":
            cat = row.category[: width - 1]
            return (base_style, f"{cat:<{width}}")

        return (base_style, f"{'':<{width}}")

    def _render_navigate(self) -> FormattedText:
        lines: list[tuple[str, str]] = []

        # Title
        lines.append(("bold", " Findings Navigator\n"))

        # Status bar
        total = len(self.visible_rows)
        all_total = len(self.all_rows)
        sort_col = self.sort_columns[self.sort_index]
        staged = len(self.staged_for_pr)
        posted = len(self.posted_indices)

        status_parts: list[str] = [f" {total}/{all_total} findings"]
        if total < all_total:
            status_parts.append("(filtered)")
        status_parts.append(f"| sort: {sort_col}")
        if staged:
            status_parts.append(f"| {staged} staged")
        if posted:
            status_parts.append(f"| {posted} posted")
        lines.append((theme.muted, " ".join(status_parts) + "\n"))

        # Status message
        if self.status_message:
            style = theme.error if self.status_message.startswith("!") else theme.success
            lines.append((style, f" {self.status_message}\n"))

        lines.append(("", "\n"))

        if not self.visible_rows:
            lines.append((theme.muted, "  No findings match current filters.\n"))
            lines.extend(self._render_footer())
            return FormattedText(lines)

        # Determine viewport
        detail_lines = 12 if self.is_detail_open else 0
        viewport_size = max(10, 28 - detail_lines)
        visible_start = max(0, self.cursor - viewport_size // 2)
        visible_end = min(len(self.visible_rows), visible_start + viewport_size)

        # Build column metadata for visible columns
        col_meta = [
            (key, label, width)
            for key, label, width in _ALL_COLUMNS
            if key in self.visible_columns
        ]

        # Table header
        header = "   " + "".join(f"{label:<{width}}" for _, label, width in col_meta)
        lines.append(("bold " + theme.muted, header + "\n"))
        total_width = sum(w for _, _, w in col_meta) + 3
        lines.append((theme.muted, "   " + "-" * total_width + "\n"))

        # Table rows
        for i in range(visible_start, visible_end):
            row = self.visible_rows[i]
            is_selected = i == self.cursor
            triage_action = self.triage.get(row.index, TriageAction.NONE)

            # Row prefix
            if is_selected:
                lines.append((theme.highlight, " > "))
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

            # Render each visible column
            for col_key, _label, width in col_meta:
                cell_style, cell_text = self._render_cell(
                    row,
                    col_key,
                    width,
                    base_style,
                    triage_action,
                )
                lines.append((cell_style, cell_text))

            lines.append(("", "\n"))

        # Detail panel
        if self.is_detail_open and self.visible_rows:
            lines.append(("", "\n"))
            lines.append((theme.muted, "   " + "=" * 90 + "\n"))
            row = self.visible_rows[self.cursor]

            lines.append(("bold", f"   {row.title}\n"))
            lines.append(("", "\n"))

            lines.append((theme.muted, "   Agent: "))
            lines.append(("", f"{row.agent_name}"))
            lines.append((theme.muted, "  |  Severity: "))
            sev_style = _SEVERITY_STYLES.get(row.severity.value, "")
            lines.append((sev_style, row.severity.value.upper()))
            lines.append((theme.muted, "  |  Confidence: "))
            lines.append(("", f"{row.confidence.value}\n"))

            if row.file_path:
                loc = row.file_path
                if row.line_number is not None:
                    loc = f"{row.file_path}:{row.line_number}"
                lines.append((theme.muted, "   File: "))
                lines.append((theme.accent, f"{loc}\n"))

            lines.append(("", "\n"))
            lines.append((theme.muted, "   Description:\n"))
            for desc_line in row.description.split("\n"):
                lines.append(("", f"     {desc_line}\n"))

            if row.suggestion:
                lines.append(("", "\n"))
                lines.append((theme.muted, "   Suggestion:\n"))
                for sug_line in row.suggestion.split("\n"):
                    lines.append((theme.success, f"     {sug_line}\n"))

        # Footer
        lines.extend(self._render_footer())

        return FormattedText(lines)

    def _render_footer(self) -> list[tuple[str, str]]:
        """Render the persistent key hints footer bar."""
        lines: list[tuple[str, str]] = []
        lines.append(("", "\n"))
        lines.append((theme.muted, " " + "-" * 90 + "\n"))

        staged = len(self.staged_for_pr)
        posted = len(self.posted_indices)

        # Key hints -- highlight actionable keys
        hints: list[tuple[str, str]] = [
            (theme.accent, " [f]"),
            ("", "ilter "),
            (theme.accent, "[s]"),
            ("", "ort "),
            (theme.accent, "[c]"),
            ("", "ols "),
            (theme.accent, "[m]"),
            ("", "ark FP "),
            (theme.accent, "[i]"),
            ("", "gnore "),
            (theme.accent, "[p]"),
            ("", "stage "),
        ]

        # Highlight P when items are staged
        p_style = "bold " + theme.accent if staged else theme.accent
        hints.extend(
            [
                (p_style, "[P]"),
                ("", "ost "),
            ]
        )

        # Highlight D when items are posted
        d_style = "bold " + theme.accent if posted else theme.muted
        hints.extend(
            [
                (d_style, "[D]"),
                ("", "elete "),
            ]
        )

        hints.extend(
            [
                (theme.accent, "[?]"),
                ("", "help "),
                (theme.accent, "[q]"),
                ("", "uit"),
            ]
        )

        lines.extend(hints)
        lines.append(("", "\n"))

        return lines

    def _render_help(self) -> FormattedText:
        """Render the help overlay with all keybindings."""
        lines: list[tuple[str, str]] = []

        lines.append(("bold", "\n  Findings Navigator -- Keyboard Reference\n"))
        lines.append(("", "\n"))

        sections: list[tuple[str, list[tuple[str, str]]]] = [
            (
                "Navigation",
                [
                    ("Up / Down", "Move between findings"),
                    ("Enter / Space", "Toggle detail panel"),
                ],
            ),
            (
                "Triage",
                [
                    ("m", "Mark / unmark as false positive"),
                    ("i", "Ignore / unignore finding"),
                ],
            ),
            (
                "PR Posting",
                [
                    ("p", "Stage / unstage finding for PR"),
                    ("P", "Post all staged findings to PR"),
                    ("D", "Delete previously posted comments"),
                ],
            ),
            (
                "View",
                [
                    ("f", "Open filter modal"),
                    ("s", "Cycle sort column"),
                    ("c", "Configure visible columns"),
                    ("?", "Show this help"),
                    ("q / Esc", "Quit to REPL"),
                ],
            ),
        ]

        for section_title, bindings in sections:
            lines.append(("bold", f"  {section_title}\n"))
            for key, desc in bindings:
                lines.append((theme.accent, f"    {key:<16}"))
                lines.append(("", f"{desc}\n"))
            lines.append(("", "\n"))

        lines.append((theme.muted, "  Press any key to dismiss.\n"))

        return FormattedText(lines)

    def _render_filter(self) -> FormattedText:
        lines: list[tuple[str, str]] = []

        lines.append(("bold", " Filter Findings\n"))
        lines.append(
            (theme.muted, " (Up/Down navigate, Enter/Space toggle, Tab confirm, Esc cancel)\n")
        )
        lines.append(("", "\n"))

        for i, (label, _key, checked) in enumerate(self.filter_options):
            is_selected = i == self.filter_cursor
            checkbox = "[x]" if checked else "[ ]"

            if is_selected:
                lines.append((theme.highlight, " > "))
            else:
                lines.append(("", "   "))

            style = "bold" if is_selected else ""
            lines.append((style, f"{checkbox} {label}\n"))

        lines.append(("", "\n"))
        checked_count = sum(1 for _, _, c in self.filter_options if c)
        total = len(self.filter_options)
        lines.append((theme.muted, f" {checked_count}/{total} selected\n"))

        return FormattedText(lines)

    def _render_columns(self) -> FormattedText:
        lines: list[tuple[str, str]] = []

        lines.append(("bold", " Column Configuration\n"))
        lines.append(
            (theme.muted, " (Up/Down navigate, Enter/Space toggle, Tab confirm, Esc cancel)\n")
        )
        lines.append(("", "\n"))

        for i, (label, _key, checked) in enumerate(self.column_options):
            is_selected = i == self.column_cursor
            checkbox = "[x]" if checked else "[ ]"

            if is_selected:
                lines.append((theme.highlight, " > "))
            else:
                lines.append(("", "   "))

            style = "bold" if is_selected else ""
            lines.append((style, f"{checkbox} {label}\n"))

        lines.append(("", "\n"))
        checked_count = sum(1 for _, _, c in self.column_options if c)
        total = len(self.column_options)
        lines.append((theme.muted, f" {checked_count}/{total} columns visible\n"))

        return FormattedText(lines)


# ---------------------------------------------------------------------------
# Command entry point
# ---------------------------------------------------------------------------


def run_findings_app(
    *,
    report: ReviewReport,
    github_token: str | None = None,
) -> None:
    """Launch the full-screen findings navigator.

    Shared entry point used by both the TUI ``findings`` command and the
    CLI ``--findings`` flag / ``findings`` subcommand.
    """
    viewer = FindingsViewer(report, github_token=github_token)

    kb = KeyBindings()

    @kb.add("up")
    def on_up(_event: KeyPressEvent) -> None:
        if viewer.mode == _ViewerMode.NAVIGATE:
            viewer.move_up()
        elif viewer.mode == _ViewerMode.FILTER:
            viewer.filter_move_up()
        elif viewer.mode == _ViewerMode.COLUMNS:
            viewer.column_move_up()

    @kb.add("down")
    def on_down(_event: KeyPressEvent) -> None:
        if viewer.mode == _ViewerMode.NAVIGATE:
            viewer.move_down()
        elif viewer.mode == _ViewerMode.FILTER:
            viewer.filter_move_down()
        elif viewer.mode == _ViewerMode.COLUMNS:
            viewer.column_move_down()

    @kb.add("enter")
    def on_enter(_event: KeyPressEvent) -> None:
        if viewer.mode == _ViewerMode.NAVIGATE:
            viewer.toggle_detail()
        elif viewer.mode == _ViewerMode.FILTER:
            viewer.filter_toggle()
        elif viewer.mode == _ViewerMode.COLUMNS:
            viewer.column_toggle()

    @kb.add("space")
    def on_space(_event: KeyPressEvent) -> None:
        if viewer.mode == _ViewerMode.NAVIGATE:
            viewer.toggle_detail()
        elif viewer.mode == _ViewerMode.FILTER:
            viewer.filter_toggle()
        elif viewer.mode == _ViewerMode.COLUMNS:
            viewer.column_toggle()

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

    @kb.add("D")  # Shift+D
    def on_delete_comments(_event: KeyPressEvent) -> None:
        if viewer.mode == _ViewerMode.NAVIGATE:
            viewer.delete_posted_comments()

    @kb.add("c")
    def on_columns(_event: KeyPressEvent) -> None:
        if viewer.mode == _ViewerMode.NAVIGATE:
            viewer.open_columns()

    @kb.add("?")
    def on_help(_event: KeyPressEvent) -> None:
        if viewer.mode == _ViewerMode.NAVIGATE:
            viewer.show_help()

    @kb.add("tab")
    def on_tab(_event: KeyPressEvent) -> None:
        if viewer.mode == _ViewerMode.FILTER:
            viewer.filter_confirm()
        elif viewer.mode == _ViewerMode.COLUMNS:
            viewer.column_confirm()

    @kb.add("escape")
    def on_escape(event: KeyPressEvent) -> None:
        if viewer.mode == _ViewerMode.HELP:
            viewer.dismiss_help()
        elif viewer.mode == _ViewerMode.FILTER:
            viewer.cancel_filter()
        elif viewer.mode == _ViewerMode.COLUMNS:
            viewer.cancel_columns()
        else:
            event.app.exit()

    @kb.add("q")
    def on_quit(event: KeyPressEvent) -> None:
        if viewer.mode == _ViewerMode.HELP:
            viewer.dismiss_help()
        elif viewer.mode == _ViewerMode.NAVIGATE:
            event.app.exit()

    @kb.add("<any>")
    def on_any(_event: KeyPressEvent) -> None:
        if viewer.mode == _ViewerMode.HELP:
            viewer.dismiss_help()

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

    # Post-TUI summary
    console = Console()
    fp_count = sum(1 for v in viewer.triage.values() if v == TriageAction.FALSE_POSITIVE)
    ign_count = sum(1 for v in viewer.triage.values() if v == TriageAction.IGNORED)

    has_activity = fp_count or ign_count or viewer.comments_posted or viewer.comments_deleted
    if has_activity:
        console.print()
        if fp_count:
            console.print(f"  {fp_count} finding(s) marked as false positive")
        if ign_count:
            console.print(f"  {ign_count} finding(s) ignored")
        if viewer.comments_posted:
            console.print(f"  {viewer.comments_posted} comment(s) posted to PR")
        if viewer.comments_deleted:
            console.print(f"  {viewer.comments_deleted} comment(s) deleted from PR")
        console.print()


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
                console.print(f"[{theme.error}]Review #{review_id} not found.[/{theme.error}]")
                return
            report = ReviewReport.model_validate_json(
                review_dict["report_json"],
            )
        except Exception as exc:
            console.print(
                f"[{theme.error}]Failed to load review #{review_id}: {exc}[/{theme.error}]"
            )
            return
    else:
        report = session.last_review_report

    if report is None:
        console.print(
            f"[{theme.warning}]No review available."
            f"Run 'review' first or specify a review ID: "
            f"findings <review_id>[/{theme.warning}]"
        )
        return

    findings = _flatten_findings(report)
    if not findings:
        console.print(f"[{theme.muted}]No findings to display.[/{theme.muted}]")
        return

    # Resolve GitHub token for PR posting
    settings = session.effective_settings
    token: str | None = None
    if settings.github_token is not None:
        token = settings.github_token.get_secret_value()

    run_findings_app(report=report, github_token=token)
