"""Tests for the interactive findings navigator."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from code_review_agent.interactive.commands.findings.actions import toggle_triage
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
from code_review_agent.interactive.commands.findings_cmd import (
    _flatten_findings,
    cmd_findings,
)
from code_review_agent.models import (
    AgentResult,
    Finding,
    ReviewReport,
    Severity,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_report(
    *,
    agent_findings: dict[str, list[Finding]] | None = None,
    pr_url: str | None = "https://github.com/acme/app/pull/42",
) -> ReviewReport:
    """Build a ReviewReport with 3 findings across 2 agents."""
    if agent_findings is None:
        agent_findings = {
            "security": [
                Finding(
                    severity="critical",
                    category="SQL Injection",
                    title="SQL injection in login",
                    description="f-string interpolation in SQL query.",
                    file_path="src/auth.py",
                    line_number=12,
                    suggestion="Use parameterized queries.",
                ),
                Finding(
                    severity="low",
                    category="Info Leak",
                    title="Stack trace exposed",
                    description="Error handler leaks internals.",
                    file_path="src/auth.py",
                    line_number=25,
                ),
            ],
            "performance": [
                Finding(
                    severity="medium",
                    category="Cache",
                    title="Unbounded LRU cache",
                    description="maxsize=256 may use too much memory.",
                    file_path="src/cache.py",
                    line_number=4,
                    suggestion="Use TTL cache.",
                ),
            ],
        }

    results = [
        AgentResult(
            agent_name=name,
            findings=findings,
            summary=f"{name} summary",
            execution_time_seconds=1.0,
        )
        for name, findings in agent_findings.items()
    ]

    return ReviewReport(
        pr_url=pr_url,
        reviewed_at=datetime(2026, 3, 15, 10, 0, 0, tzinfo=UTC),
        agent_results=results,
        overall_summary="Test summary.",
        risk_level="high",
    )


@pytest.fixture
def report() -> ReviewReport:
    return _make_report()


@pytest.fixture
def viewer(report: ReviewReport) -> FindingsViewer:
    # Build rows with finding_db_id set so triage/posted operations work
    rows = _flatten_findings(report)
    rows_with_ids = [
        FindingRow(
            finding_db_id=i + 1,
            review_id=1,
            index=row.index,
            severity=row.severity,
            agent_name=row.agent_name,
            category=row.category,
            title=row.title,
            description=row.description,
            file_path=row.file_path,
            line_number=row.line_number,
            suggestion=row.suggestion,
            confidence=row.confidence,
            repo=row.repo,
            pr_number=row.pr_number,
        )
        for i, row in enumerate(rows)
    ]
    return FindingsViewer(rows=rows_with_ids, report=report, github_token="ghp_test_token")


# ---------------------------------------------------------------------------
# TestFlattenFindings
# ---------------------------------------------------------------------------


class TestFlattenFindings:
    def test_produces_correct_count(self, report: ReviewReport) -> None:
        rows = _flatten_findings(report)
        assert len(rows) == 3

    def test_assigns_agent_names(self, report: ReviewReport) -> None:
        rows = _flatten_findings(report)
        agents = {r.agent_name for r in rows}
        assert agents == {"security", "performance"}

    def test_assigns_sequential_indices(self, report: ReviewReport) -> None:
        rows = _flatten_findings(report)
        indices = [r.index for r in rows]
        assert indices == [0, 1, 2]

    def test_empty_report(self) -> None:
        report = _make_report(agent_findings={"security": []})
        rows = _flatten_findings(report)
        assert rows == []

    def test_repo_and_pr_from_url(self, report: ReviewReport) -> None:
        rows = _flatten_findings(report)
        assert rows[0].repo == "acme/app"
        assert rows[0].pr_number == 42

    def test_no_pr_url(self) -> None:
        report = _make_report(pr_url=None)
        rows = _flatten_findings(report)
        assert rows[0].repo is None
        assert rows[0].pr_number is None


# ---------------------------------------------------------------------------
# TestNavigation
# ---------------------------------------------------------------------------


class TestNavigation:
    def test_move_down(self, viewer: FindingsViewer) -> None:
        assert viewer.cursor == 0
        viewer.move_down()
        assert viewer.cursor == 1

    def test_move_up_at_zero(self, viewer: FindingsViewer) -> None:
        viewer.move_up()
        assert viewer.cursor == 0

    def test_move_down_at_end(self, viewer: FindingsViewer) -> None:
        viewer.cursor = len(viewer.visible_rows) - 1
        viewer.move_down()
        assert viewer.cursor == len(viewer.visible_rows) - 1

    def test_scroll_left_at_zero(self, viewer: FindingsViewer) -> None:
        assert viewer.h_offset == 0
        viewer.scroll_left()
        assert viewer.h_offset == 0

    def test_scroll_right(self, viewer: FindingsViewer) -> None:
        viewer.scroll_right()
        # Offset increases if there is scrollable content, stays at 0 otherwise
        assert viewer.h_offset >= 0

    def test_open_detail(self, viewer: FindingsViewer) -> None:
        assert viewer.is_detail_open is False
        assert viewer.mode == ViewerMode.NAVIGATE
        viewer.open_detail()
        assert viewer.is_detail_open is True
        assert viewer.mode == ViewerMode.DETAIL

    def test_close_detail(self, viewer: FindingsViewer) -> None:
        viewer.open_detail()
        viewer.close_detail()
        assert viewer.is_detail_open is False
        assert viewer.mode == ViewerMode.NAVIGATE


# ---------------------------------------------------------------------------
# TestSort
# ---------------------------------------------------------------------------


class TestSort:
    def test_cycle_sort_changes_column(self, viewer: FindingsViewer) -> None:
        assert viewer.sort_index == 0
        viewer.cycle_sort()
        assert viewer.sort_index == 1

    def test_sort_wraps_and_reverses(self, viewer: FindingsViewer) -> None:
        num_cols = len(viewer.sort_columns)
        for _ in range(num_cols - 1):
            viewer.cycle_sort()
        assert viewer.sort_index == num_cols - 1
        assert viewer.is_sort_reversed is False
        viewer.cycle_sort()
        assert viewer.sort_index == 0
        assert viewer.is_sort_reversed is True

    def test_sort_by_severity(self, viewer: FindingsViewer) -> None:
        viewer._apply_sort()
        severities = [r.severity for r in viewer.visible_rows]
        assert severities[0] == Severity.CRITICAL

    def test_sort_by_agent(self, viewer: FindingsViewer) -> None:
        viewer.sort_index = 1  # agent_name
        viewer._apply_sort()
        agents = [r.agent_name for r in viewer.visible_rows]
        assert agents == sorted(agents)


# ---------------------------------------------------------------------------
# TestTriage
# ---------------------------------------------------------------------------


class TestTriage:
    def test_toggle_solved(self, viewer: FindingsViewer) -> None:
        toggle_triage(viewer, TriageAction.SOLVED)
        row = viewer.all_rows[0]
        assert viewer.triage[row.finding_db_id] == TriageAction.SOLVED

    def test_toggle_solved_twice_resets(self, viewer: FindingsViewer) -> None:
        toggle_triage(viewer, TriageAction.SOLVED)
        row = viewer.all_rows[0]
        # Re-add to visible so toggle can find it
        viewer.visible_rows = list(viewer.all_rows)
        viewer.cursor = 0
        toggle_triage(viewer, TriageAction.SOLVED)
        assert viewer.triage[row.finding_db_id] == TriageAction.OPEN

    def test_toggle_false_positive(self, viewer: FindingsViewer) -> None:
        toggle_triage(viewer, TriageAction.FALSE_POSITIVE)
        row = viewer.all_rows[0]
        assert viewer.triage[row.finding_db_id] == TriageAction.FALSE_POSITIVE

    def test_toggle_ignored(self, viewer: FindingsViewer) -> None:
        toggle_triage(viewer, TriageAction.IGNORED)
        row = viewer.all_rows[0]
        assert viewer.triage[row.finding_db_id] == TriageAction.IGNORED

    def test_triage_on_empty_rows(self) -> None:
        report = _make_report(agent_findings={"security": []})
        viewer = FindingsViewer(report=report)
        toggle_triage(viewer, TriageAction.SOLVED)
        # Should not raise; triage unchanged from init defaults
        assert not viewer.visible_rows


# ---------------------------------------------------------------------------
# TestFilter
# ---------------------------------------------------------------------------


class TestFilter:
    def test_add_filter(self, viewer: FindingsViewer) -> None:
        viewer.add_filter("severity", "critical")
        assert len(viewer.active_filters) == 1
        assert viewer.active_filters[0].field == "severity"
        assert "critical" in viewer.active_filters[0].values

    def test_add_filter_merges_same_field(self, viewer: FindingsViewer) -> None:
        viewer.add_filter("severity", "critical")
        viewer.add_filter("severity", "high")
        assert len(viewer.active_filters) == 1
        assert viewer.active_filters[0].values == {"critical", "high"}

    def test_remove_filter(self, viewer: FindingsViewer) -> None:
        viewer.add_filter("severity", "critical")
        viewer.remove_filter(0)
        assert len(viewer.active_filters) == 0

    def test_clear_filters(self, viewer: FindingsViewer) -> None:
        viewer.add_filter("severity", "critical")
        viewer.add_filter("agent_name", "security")
        viewer.clear_filters()
        assert viewer.active_filters == []
        assert len(viewer.visible_rows) == len(viewer.all_rows)

    def test_apply_filters_hides_solved_by_default(self, viewer: FindingsViewer) -> None:
        row = viewer.visible_rows[0]
        viewer.triage[row.finding_db_id] = TriageAction.SOLVED
        viewer._apply_filters()
        visible_ids = {r.finding_db_id for r in viewer.visible_rows}
        assert row.finding_db_id not in visible_ids

    def test_open_filter_sets_mode(self, viewer: FindingsViewer) -> None:
        viewer.open_filter()
        assert viewer.mode == ViewerMode.FILTER
        assert viewer.filter_dimension == "severity"

    def test_cancel_filter_returns_to_navigate(self, viewer: FindingsViewer) -> None:
        viewer.open_filter()
        viewer.cancel_filter()
        assert viewer.mode == ViewerMode.NAVIGATE


# ---------------------------------------------------------------------------
# TestRendering
# ---------------------------------------------------------------------------


class TestRendering:
    def _is_formatted_text(self, result: list[tuple[str, str]]) -> bool:
        return isinstance(result, list) and all(
            isinstance(item, tuple) and len(item) == 2 for item in result
        )

    def test_render_header(self, viewer: FindingsViewer) -> None:
        result = render_header(viewer)
        assert self._is_formatted_text(result)
        text = "".join(t for _, t in result)
        assert "Findings Navigator" in text

    def test_render_table(self, viewer: FindingsViewer) -> None:
        result = render_table(viewer, term_width=120)
        assert self._is_formatted_text(result)
        text = "".join(t for _, t in result)
        assert "Sev" in text

    def test_render_table_empty(self) -> None:
        report = _make_report(agent_findings={"security": []})
        viewer = FindingsViewer(report=report)
        result = render_table(viewer, term_width=120)
        text = "".join(t for _, t in result)
        assert "No findings" in text

    def test_render_detail(self, viewer: FindingsViewer) -> None:
        result = render_detail(viewer)
        assert self._is_formatted_text(result)
        text = "".join(t for _, t in result)
        assert "SQL injection in login" in text

    def test_render_detail_empty(self) -> None:
        report = _make_report(agent_findings={"security": []})
        viewer = FindingsViewer(report=report)
        result = render_detail(viewer)
        assert result == []

    def test_render_footer(self, viewer: FindingsViewer) -> None:
        result = render_footer(viewer)
        assert self._is_formatted_text(result)
        text = "".join(t for _, t in result)
        assert "[f]" in text
        assert "[s]" in text

    def test_render_help(self, viewer: FindingsViewer) -> None:
        result = render_help(viewer)
        assert self._is_formatted_text(result)
        text = "".join(t for _, t in result)
        assert "Keyboard Reference" in text
        assert "Press any key" in text

    def test_render_filter(self, viewer: FindingsViewer) -> None:
        viewer.open_filter()
        result = render_filter(viewer)
        assert self._is_formatted_text(result)
        text = "".join(t for _, t in result)
        assert "Filter Findings" in text

    def test_render_confirm(self, viewer: FindingsViewer) -> None:
        viewer.request_confirm("delete", "Delete this finding?")
        result = render_confirm(viewer)
        assert self._is_formatted_text(result)
        text = "".join(t for _, t in result)
        assert "Delete this finding?" in text
        assert "[y] Yes" in text

    def test_render_confirm_empty_when_no_pending(self, viewer: FindingsViewer) -> None:
        result = render_confirm(viewer)
        assert result == []


# ---------------------------------------------------------------------------
# TestCmdFindings
# ---------------------------------------------------------------------------


class TestCmdFindings:
    def test_no_findings_shows_message(self) -> None:
        session = MagicMock()
        session.effective_settings.history_db_path = ":memory:"
        session.effective_settings.github_token = None

        with (
            patch(
                "code_review_agent.interactive.commands.findings_cmd.Console"
            ) as mock_console_cls,
            patch("code_review_agent.storage.ReviewStorage") as mock_storage_cls,
        ):
            mock_console = MagicMock()
            mock_console_cls.return_value = mock_console
            mock_storage = MagicMock()
            mock_storage.load_all_findings.return_value = []
            mock_storage_cls.return_value = mock_storage
            cmd_findings([], session)

        mock_console.print.assert_called_once()
        call_args = mock_console.print.call_args[0][0]
        assert "No findings" in call_args

    def test_loads_review_by_id(self) -> None:
        session = MagicMock()
        session.effective_settings.history_db_path = "~/.cra/reviews.db"
        session.effective_settings.github_token = None

        mock_storage = MagicMock()
        mock_storage.load_findings_for_review.return_value = [
            {
                "id": 1,
                "review_id": 1,
                "finding_index": 0,
                "severity": "high",
                "agent_name": "security",
                "category": "security",
                "title": "SQL injection",
                "description": "Test finding",
                "file_path": "src/db.py",
                "line_number": 10,
                "suggestion": "Fix it",
                "confidence": "medium",
                "repo": "acme/app",
                "pr_number": 42,
                "triage_action": "open",
                "is_posted": 0,
            },
        ]

        with (
            patch(
                "code_review_agent.storage.ReviewStorage",
                return_value=mock_storage,
            ),
            patch(
                "code_review_agent.interactive.commands.findings_cmd.run_findings_app",
            ) as mock_run,
            patch("code_review_agent.interactive.commands.findings_cmd.Console"),
        ):
            cmd_findings(["1"], session)

        mock_storage.load_findings_for_review.assert_called_once_with(1)
        mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# TestConfirm
# ---------------------------------------------------------------------------


class TestConfirm:
    def test_request_confirm(self, viewer: FindingsViewer) -> None:
        viewer.request_confirm("delete", "Delete this finding?")
        assert viewer.mode == ViewerMode.CONFIRM
        assert viewer.pending_confirm is not None
        assert viewer.pending_confirm.action == "delete"
        assert viewer.pending_confirm.description == "Delete this finding?"

    def test_request_confirm_empty_rows(self) -> None:
        report = _make_report(agent_findings={"security": []})
        viewer = FindingsViewer(report=report)
        viewer.request_confirm("delete", "Delete?")
        assert viewer.mode == ViewerMode.NAVIGATE
        assert viewer.pending_confirm is None

    def test_confirm_yes(self, viewer: FindingsViewer) -> None:
        viewer.request_confirm("delete", "Delete?")
        result = viewer.confirm_yes()
        assert result is not None
        assert result.action == "delete"
        assert viewer.pending_confirm is None
        assert viewer.mode == ViewerMode.NAVIGATE

    def test_confirm_yes_returns_to_detail_if_open(self, viewer: FindingsViewer) -> None:
        viewer.open_detail()
        viewer.request_confirm("delete", "Delete?")
        viewer.confirm_yes()
        assert viewer.mode == ViewerMode.DETAIL

    def test_confirm_no(self, viewer: FindingsViewer) -> None:
        viewer.request_confirm("delete", "Delete?")
        viewer.confirm_no()
        assert viewer.pending_confirm is None
        assert viewer.mode == ViewerMode.NAVIGATE

    def test_confirm_no_returns_to_detail_if_open(self, viewer: FindingsViewer) -> None:
        viewer.open_detail()
        viewer.request_confirm("delete", "Delete?")
        viewer.confirm_no()
        assert viewer.mode == ViewerMode.DETAIL
        assert viewer.is_detail_open is True
