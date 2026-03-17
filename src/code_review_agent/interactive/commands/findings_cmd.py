"""Findings navigator entry point.

Thin wrapper that wires together the findings/ package and launches
the full-screen TUI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prompt_toolkit import Application
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    Float,
    FloatContainer,
    HSplit,
    Window,
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.layout import Layout
from rich.console import Console

from code_review_agent.interactive.commands.findings.keybindings import (
    build_key_bindings,
)
from code_review_agent.interactive.commands.findings.models import (
    FindingRow,
    TriageAction,
    ViewerMode,
)
from code_review_agent.interactive.commands.findings.renderer import (
    render_confirm,
    render_detail,
    render_filter,
    render_footer,
    render_header,
    render_help,
    render_table,
)
from code_review_agent.interactive.commands.findings.state import FindingsViewer
from code_review_agent.models import Confidence, ReviewReport, Severity
from code_review_agent.theme import theme

if TYPE_CHECKING:
    from code_review_agent.interactive.session import SessionState
    from code_review_agent.storage import ReviewStorage


def _flatten_findings(report: ReviewReport) -> list[FindingRow]:
    """Extract all findings from a report into a flat list of FindingRow."""
    from code_review_agent.github_client import parse_pr_reference

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


def _rows_from_db(db_rows: list[dict[str, Any]]) -> list[FindingRow]:
    """Convert database finding dicts into FindingRow objects."""
    return [
        FindingRow(
            finding_db_id=row["id"],
            review_id=row["review_id"],
            index=row["finding_index"],
            severity=Severity(row["severity"]),
            agent_name=row["agent_name"],
            category=row["category"],
            title=row["title"],
            description=row["description"],
            file_path=row.get("file_path"),
            line_number=row.get("line_number"),
            suggestion=row.get("suggestion"),
            confidence=Confidence(row.get("confidence", "medium")),
            repo=row.get("repo"),
            pr_number=row.get("pr_number"),
            triage_action=row.get("triage_action", "open"),
            is_posted=bool(row.get("is_posted", 0)),
        )
        for row in db_rows
    ]


def run_findings_app(
    *,
    rows: list[FindingRow] | None = None,
    report: ReviewReport | None = None,
    github_token: str | None = None,
    storage: ReviewStorage | None = None,
) -> None:
    """Launch the full-screen findings navigator."""
    import shutil

    from prompt_toolkit.filters import Condition

    viewer = FindingsViewer(
        rows=rows,
        report=report,
        github_token=github_token,
        storage=storage,
    )

    def get_term_width() -> int:
        return shutil.get_terminal_size((120, 40)).columns

    # Controls
    header_control = FormattedTextControl(lambda: render_header(viewer))
    table_control = FormattedTextControl(lambda: render_table(viewer, get_term_width()))
    detail_control = FormattedTextControl(lambda: render_detail(viewer))
    footer_control = FormattedTextControl(lambda: render_footer(viewer))
    filter_control = FormattedTextControl(lambda: render_filter(viewer))
    help_control = FormattedTextControl(lambda: render_help(viewer))
    confirm_control = FormattedTextControl(lambda: render_confirm(viewer))

    is_detail = Condition(
        lambda: viewer.mode in (ViewerMode.DETAIL, ViewerMode.CONFIRM),
    )
    is_filter = Condition(lambda: viewer.mode == ViewerMode.FILTER)
    is_help = Condition(lambda: viewer.mode == ViewerMode.HELP)
    is_confirm = Condition(lambda: viewer.mode == ViewerMode.CONFIRM)

    body = FloatContainer(
        content=HSplit(
            [
                Window(header_control, height=4, wrap_lines=True),
                Window(table_control, wrap_lines=False),
                ConditionalContainer(
                    Window(
                        detail_control,
                        height=Dimension(min=8, max=20),
                        wrap_lines=True,
                    ),
                    filter=is_detail,
                ),
                Window(footer_control, height=3, wrap_lines=True),
            ]
        ),
        floats=[
            Float(
                ConditionalContainer(
                    Window(filter_control, wrap_lines=True),
                    filter=is_filter,
                ),
                top=3,
                left=2,
                right=2,
                bottom=4,
            ),
            Float(
                ConditionalContainer(
                    Window(help_control, wrap_lines=True),
                    filter=is_help,
                ),
                top=1,
                left=1,
                right=1,
                bottom=1,
            ),
            Float(
                ConditionalContainer(
                    Window(confirm_control, height=9, width=50),
                    filter=is_confirm,
                ),
            ),
        ],
    )

    kb = build_key_bindings(viewer)
    layout = Layout(body)

    app: Application[None] = Application(
        layout=layout,
        key_bindings=kb,
        full_screen=True,
        refresh_interval=0.1,
    )
    app.run()

    # Post-TUI summary
    console = Console()
    solved = sum(1 for v in viewer.triage.values() if v == TriageAction.SOLVED)
    fp = sum(1 for v in viewer.triage.values() if v == TriageAction.FALSE_POSITIVE)
    ign = sum(1 for v in viewer.triage.values() if v == TriageAction.IGNORED)

    has_activity = solved or fp or ign or viewer.comments_posted or viewer.comments_deleted
    if has_activity:
        console.print()
        if solved:
            console.print(f"  {solved} finding(s) marked as solved")
        if fp:
            console.print(f"  {fp} finding(s) marked as false positive")
        if ign:
            console.print(f"  {ign} finding(s) ignored")
        if viewer.comments_posted:
            console.print(f"  {viewer.comments_posted} comment(s) posted to PR")
        if viewer.comments_deleted:
            console.print(f"  {viewer.comments_deleted} comment(s) deleted from PR")
        console.print()


def cmd_findings(args: list[str], session: SessionState) -> None:
    """Launch the interactive findings navigator.

    Without args: loads ALL unsolved findings from the database.
    With a numeric arg: loads findings for that review ID.
    """
    from code_review_agent.storage import ReviewStorage

    console = Console()
    settings = session.effective_settings
    storage = ReviewStorage(settings.history_db_path)

    if args and args[0].isdigit():
        review_id = int(args[0])
        try:
            db_rows = storage.load_findings_for_review(review_id)
        except Exception as exc:
            console.print(
                f"[{theme.error}]Failed to load review #{review_id}: {exc}[/{theme.error}]"
            )
            return
        if not db_rows:
            console.print(f"[{theme.error}]Review #{review_id} has no findings.[/{theme.error}]")
            return
        rows = _rows_from_db(db_rows)
    else:
        try:
            db_rows = storage.load_unsolved_findings()
        except Exception as exc:
            console.print(f"[{theme.error}]Failed to load findings: {exc}[/{theme.error}]")
            return
        if not db_rows:
            console.print(f"[{theme.muted}]No unsolved findings.[/{theme.muted}]")
            return
        rows = _rows_from_db(db_rows)

    token: str | None = None
    if settings.github_token is not None:
        token = settings.github_token.get_secret_value()

    run_findings_app(
        rows=rows,
        github_token=token,
        storage=storage,
    )
