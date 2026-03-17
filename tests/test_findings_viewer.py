"""Tests for the interactive findings navigator (CRA-75)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from code_review_agent.interactive.commands.findings_cmd import (
    FindingsViewer,
    TriageAction,
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
    """Build a ReviewReport with given agent findings."""
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
            "style": [],
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
    return FindingsViewer(report, github_token="ghp_test_token")


# ---------------------------------------------------------------------------
# _flatten_findings
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


# ---------------------------------------------------------------------------
# Navigation
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

    def test_toggle_detail(self, viewer: FindingsViewer) -> None:
        assert viewer.is_detail_open is False
        viewer.toggle_detail()
        assert viewer.is_detail_open is True
        viewer.toggle_detail()
        assert viewer.is_detail_open is False


# ---------------------------------------------------------------------------
# Sort
# ---------------------------------------------------------------------------


class TestSort:
    def test_cycle_sort_changes_column(self, viewer: FindingsViewer) -> None:
        assert viewer.sort_index == 0
        viewer.cycle_sort()
        assert viewer.sort_index == 1

    def test_sort_by_severity(self, viewer: FindingsViewer) -> None:
        # Default sort_index=0 is severity
        viewer._apply_sort()
        severities = [r.severity for r in viewer.visible_rows]
        assert severities[0] == Severity.CRITICAL

    def test_sort_by_agent(self, viewer: FindingsViewer) -> None:
        viewer.sort_index = 1  # agent_name
        viewer._apply_sort()
        agents = [r.agent_name for r in viewer.visible_rows]
        assert agents == sorted(agents)

    def test_sort_resets_cursor(self, viewer: FindingsViewer) -> None:
        viewer.cursor = 2
        viewer.cycle_sort()
        assert viewer.cursor == 0


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------


class TestFilter:
    def test_filter_by_severity(self, viewer: FindingsViewer) -> None:
        viewer.filter_severity = {Severity.CRITICAL}
        viewer._apply_filters()
        assert len(viewer.visible_rows) == 1
        assert viewer.visible_rows[0].severity == Severity.CRITICAL

    def test_filter_by_agent(self, viewer: FindingsViewer) -> None:
        viewer.filter_agents = {"performance"}
        viewer._apply_filters()
        assert len(viewer.visible_rows) == 1
        assert viewer.visible_rows[0].agent_name == "performance"

    def test_filter_clamps_cursor(self, viewer: FindingsViewer) -> None:
        viewer.cursor = 2
        viewer.filter_severity = {Severity.CRITICAL}
        viewer._apply_filters()
        assert viewer.cursor == 0

    def test_open_filter_builds_options(self, viewer: FindingsViewer) -> None:
        viewer.open_filter()
        # 4 severity + 3 agents (security, performance, style) + 4 triage
        assert len(viewer.filter_options) == 11

    def test_filter_toggle_and_confirm(self, viewer: FindingsViewer) -> None:
        viewer.open_filter()
        # Uncheck all severities except first (critical)
        for i, (_label, key, _) in enumerate(viewer.filter_options):
            if key.startswith("sev:") and key != "sev:critical":
                viewer.filter_cursor = i
                viewer.filter_toggle()
        viewer.filter_confirm()
        assert viewer.filter_severity == {Severity.CRITICAL}


# ---------------------------------------------------------------------------
# Triage
# ---------------------------------------------------------------------------


class TestTriage:
    def test_mark_false_positive_toggle(self, viewer: FindingsViewer) -> None:
        viewer.mark_false_positive()
        assert viewer.triage[0] == TriageAction.FALSE_POSITIVE
        viewer.mark_false_positive()
        assert 0 not in viewer.triage

    def test_mark_ignored_toggle(self, viewer: FindingsViewer) -> None:
        viewer.mark_ignored()
        assert viewer.triage[0] == TriageAction.IGNORED
        viewer.mark_ignored()
        assert 0 not in viewer.triage

    def test_triage_on_empty_rows(self) -> None:
        report = _make_report(agent_findings={"security": []})
        viewer = FindingsViewer(report)
        viewer.mark_false_positive()  # should not raise
        assert viewer.triage == {}


# ---------------------------------------------------------------------------
# PR staging
# ---------------------------------------------------------------------------


class TestPrStaging:
    def test_toggle_stage(self, viewer: FindingsViewer) -> None:
        viewer.toggle_stage_for_pr()
        assert 0 in viewer.staged_for_pr
        viewer.toggle_stage_for_pr()
        assert 0 not in viewer.staged_for_pr

    def test_stage_empty_rows(self) -> None:
        report = _make_report(agent_findings={"security": []})
        viewer = FindingsViewer(report)
        viewer.toggle_stage_for_pr()  # should not raise
        assert viewer.staged_for_pr == set()


# ---------------------------------------------------------------------------
# PR posting
# ---------------------------------------------------------------------------


class TestPrPosting:
    def test_no_pr_url(self) -> None:
        report = _make_report(pr_url=None)
        viewer = FindingsViewer(report, github_token="tok")
        viewer.staged_for_pr = {0}
        viewer.submit_to_pr()
        assert "Not a PR review" in viewer.status_message

    def test_no_github_token(self, report: ReviewReport) -> None:
        viewer = FindingsViewer(report, github_token=None)
        viewer.staged_for_pr = {0}
        viewer.submit_to_pr()
        assert "GITHUB_TOKEN required" in viewer.status_message

    def test_no_staged_findings(self, viewer: FindingsViewer) -> None:
        viewer.submit_to_pr()
        assert "No findings staged" in viewer.status_message

    def test_builds_correct_comment_payload(self, viewer: FindingsViewer) -> None:
        viewer.staged_for_pr = {0, 2}  # security finding + performance finding
        with patch(
            "code_review_agent.interactive.commands.findings_cmd.submit_pr_review_with_comments"
        ) as mock_submit:
            mock_submit.return_value = {
                "id": 1,
                "state": "COMMENTED",
                "html_url": "https://github.com/...",
                "comments_posted": 2,
            }
            viewer.submit_to_pr()

        mock_submit.assert_called_once()
        call_kwargs = mock_submit.call_args.kwargs
        assert call_kwargs["owner"] == "acme"
        assert call_kwargs["repo"] == "app"
        assert call_kwargs["pr_number"] == 42
        assert len(call_kwargs["comments"]) == 2
        assert call_kwargs["comments"][0]["path"] == "src/auth.py"
        assert call_kwargs["comments"][0]["line"] == 12

    def test_findings_without_location_go_in_body(self) -> None:
        """Findings without file/line are included in the review body."""
        report = _make_report(
            agent_findings={
                "security": [
                    Finding(
                        severity="high",
                        category="General",
                        title="No file path",
                        description="A general finding.",
                    ),
                ],
                "style": [
                    Finding(
                        severity="low",
                        category="Style",
                        title="Has file",
                        description="Style issue.",
                        file_path="src/app.py",
                        line_number=10,
                    ),
                ],
            },
        )
        viewer = FindingsViewer(report, github_token="tok")
        viewer.staged_for_pr = {0, 1}

        with patch(
            "code_review_agent.interactive.commands.findings_cmd.submit_pr_review_with_comments"
        ) as mock_submit:
            mock_submit.return_value = {
                "id": 1,
                "state": "COMMENTED",
                "html_url": "",
                "comments_posted": 1,
            }
            viewer.submit_to_pr()

        call_kwargs = mock_submit.call_args.kwargs
        # Only 1 inline comment (the one with file/line)
        assert len(call_kwargs["comments"]) == 1
        # The finding without location is in the body
        assert "No file path" in call_kwargs["body"]

    def test_handles_auth_error(self, viewer: FindingsViewer) -> None:
        from code_review_agent.github_client import GitHubAuthError

        viewer.staged_for_pr = {0}
        with patch(
            "code_review_agent.interactive.commands.findings_cmd.submit_pr_review_with_comments",
            side_effect=GitHubAuthError("403"),
        ):
            viewer.submit_to_pr()

        assert "Permission denied" in viewer.status_message

    def test_handles_http_error(self, viewer: FindingsViewer) -> None:
        import httpx

        viewer.staged_for_pr = {0}
        mock_response = MagicMock()
        mock_response.status_code = 404
        with patch(
            "code_review_agent.interactive.commands.findings_cmd.submit_pr_review_with_comments",
            side_effect=httpx.HTTPStatusError(
                "Not Found",
                request=MagicMock(),
                response=mock_response,
            ),
        ):
            viewer.submit_to_pr()

        assert "404" in viewer.status_message

    def test_clears_staged_on_success(self, viewer: FindingsViewer) -> None:
        viewer.staged_for_pr = {0, 2}
        with (
            patch(
                "code_review_agent.interactive.commands.findings_cmd"
                ".submit_pr_review_with_comments"
            ) as mock_submit,
            patch(
                "code_review_agent.interactive.commands.findings_cmd.get_review_comments",
                return_value=[{"id": 1001}, {"id": 1002}],
            ),
        ):
            mock_submit.return_value = {
                "id": 100,
                "state": "COMMENTED",
                "html_url": "",
                "comments_posted": 2,
            }
            viewer.submit_to_pr()

        assert viewer.staged_for_pr == set()
        assert viewer.comments_posted == 2
        assert viewer.last_review_id == 100
        assert viewer.last_comment_ids == [1001, 1002]

    def test_posted_indices_tracked(self, viewer: FindingsViewer) -> None:
        """Posted finding indices are recorded for status display."""
        viewer.staged_for_pr = {0, 2}
        with (
            patch(
                "code_review_agent.interactive.commands.findings_cmd"
                ".submit_pr_review_with_comments"
            ) as mock_submit,
            patch(
                "code_review_agent.interactive.commands.findings_cmd.get_review_comments",
                return_value=[{"id": 1001}],
            ),
        ):
            mock_submit.return_value = {
                "id": 100,
                "state": "COMMENTED",
                "html_url": "",
                "comments_posted": 2,
            }
            viewer.submit_to_pr()

        assert viewer.posted_indices == {0, 2}

    def test_blocks_double_posting(self, viewer: FindingsViewer) -> None:
        """Cannot re-post if comments already exist -- must delete first."""
        viewer.last_comment_ids = [1001, 1002]
        viewer.staged_for_pr = {0}
        viewer.submit_to_pr()
        assert "delete first" in viewer.status_message.lower()

    def test_delete_clears_posted_indices(self, viewer: FindingsViewer) -> None:
        viewer.last_comment_ids = [1001, 1002]
        viewer.last_review_id = 100
        viewer.posted_indices = {0, 2}
        with patch(
            "code_review_agent.interactive.commands.findings_cmd.delete_review_comments",
            return_value=2,
        ):
            viewer.delete_posted_comments()

        assert viewer.last_comment_ids == []
        assert viewer.last_review_id is None
        assert viewer.posted_indices == set()
        assert viewer.comments_deleted == 2
        assert "Deleted 2" in viewer.status_message

    def test_delete_with_no_posted_comments(self, viewer: FindingsViewer) -> None:
        viewer.delete_posted_comments()
        assert "No posted comments" in viewer.status_message


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


class TestRendering:
    def test_render_returns_formatted_text(self, viewer: FindingsViewer) -> None:
        result = viewer.render()
        assert isinstance(result, list)
        assert all(isinstance(item, tuple) and len(item) == 2 for item in result)

    def test_render_filter_mode(self, viewer: FindingsViewer) -> None:
        viewer.open_filter()
        result = viewer.render()
        # Should contain filter-related text
        text = "".join(t for _, t in result)
        assert "Filter Findings" in text

    def test_render_empty_findings(self) -> None:
        report = _make_report(agent_findings={"security": []})
        viewer = FindingsViewer(report)
        result = viewer.render()
        text = "".join(t for _, t in result)
        assert "No findings" in text

    def test_render_help_mode(self, viewer: FindingsViewer) -> None:
        viewer.show_help()
        result = viewer.render()
        text = "".join(t for _, t in result)
        assert "Keyboard Reference" in text
        assert "Mark / unmark" in text
        assert "Press any key" in text

    def test_dismiss_help(self, viewer: FindingsViewer) -> None:
        viewer.show_help()
        assert viewer.mode == "help"
        viewer.dismiss_help()
        assert viewer.mode == "navigate"

    def test_footer_contains_key_hints(self, viewer: FindingsViewer) -> None:
        result = viewer.render()
        text = "".join(t for _, t in result)
        assert "[f]" in text
        assert "[s]" in text
        assert "[p]" in text
        assert "[P]" in text
        assert "[D]" in text
        assert "[?]" in text
        assert "[q]" in text

    def test_triage_column_in_table(self, viewer: FindingsViewer) -> None:
        viewer.mark_false_positive()  # marks index 0
        result = viewer.render()
        text = "".join(t for _, t in result)
        assert "[FP]" in text

    def test_pr_status_column_staged(self, viewer: FindingsViewer) -> None:
        viewer.toggle_stage_for_pr()  # stages index 0
        result = viewer.render()
        text = "".join(t for _, t in result)
        assert "[STAGED]" in text

    def test_pr_status_column_posted(self, viewer: FindingsViewer) -> None:
        viewer.posted_indices = {0}
        result = viewer.render()
        text = "".join(t for _, t in result)
        assert "[POSTED]" in text

    def test_table_header_has_triage_and_pr_columns(self, viewer: FindingsViewer) -> None:
        result = viewer.render()
        text = "".join(t for _, t in result)
        assert "Triage" in text
        assert "PR" in text


# ---------------------------------------------------------------------------
# Column configuration
# ---------------------------------------------------------------------------


class TestColumnConfig:
    def test_open_columns_builds_options(self, viewer: FindingsViewer) -> None:
        viewer.open_columns()
        assert viewer.mode == "columns"
        assert len(viewer.column_options) == 10  # all available columns

    def test_column_toggle(self, viewer: FindingsViewer) -> None:
        viewer.open_columns()
        # Find "repo" column (should be unchecked by default)
        repo_idx = next(i for i, (_, key, _) in enumerate(viewer.column_options) if key == "repo")
        viewer.column_cursor = repo_idx
        viewer.column_toggle()
        assert viewer.column_options[repo_idx][2] is True

    def test_column_confirm_updates_visible(self, viewer: FindingsViewer) -> None:
        viewer.open_columns()
        # Toggle repo on
        repo_idx = next(i for i, (_, key, _) in enumerate(viewer.column_options) if key == "repo")
        viewer.column_cursor = repo_idx
        viewer.column_toggle()
        viewer.column_confirm()
        assert "repo" in viewer.visible_columns
        assert viewer.mode == "navigate"

    def test_column_cancel_preserves_state(self, viewer: FindingsViewer) -> None:
        original = list(viewer.visible_columns)
        viewer.open_columns()
        viewer.cancel_columns()
        assert viewer.visible_columns == original
        assert viewer.mode == "navigate"

    def test_render_columns_mode(self, viewer: FindingsViewer) -> None:
        viewer.open_columns()
        result = viewer.render()
        text = "".join(t for _, t in result)
        assert "Column Configuration" in text
        assert "columns visible" in text

    def test_dynamic_table_header(self, viewer: FindingsViewer) -> None:
        viewer.visible_columns = ["severity", "title"]
        result = viewer.render()
        text = "".join(t for _, t in result)
        assert "Sev" in text
        assert "Title" in text
        # Agent should not appear
        assert "Agent" not in text.split("\n")[3]  # header line


# ---------------------------------------------------------------------------
# Advanced filters
# ---------------------------------------------------------------------------


class TestAdvancedFilters:
    def test_filter_includes_triage_when_triaged(self, viewer: FindingsViewer) -> None:
        viewer.mark_false_positive()  # triage index 0
        viewer.open_filter()
        keys = [key for _, key, _ in viewer.filter_options]
        assert "triage:none" in keys
        assert "triage:false_positive" in keys

    def test_filter_always_shows_triage_options(self, viewer: FindingsViewer) -> None:
        viewer.open_filter()
        keys = [key for _, key, _ in viewer.filter_options]
        assert "triage:none" in keys
        assert "triage:solved" in keys

    def test_filter_includes_pr_status_when_staged(self, viewer: FindingsViewer) -> None:
        viewer.toggle_stage_for_pr()  # stage index 0
        viewer.open_filter()
        keys = [key for _, key, _ in viewer.filter_options]
        assert "prstatus:staged" in keys

    def test_filter_by_triage(self, viewer: FindingsViewer) -> None:
        viewer.mark_false_positive()  # mark index 0 as FP
        viewer.filter_triage = {"false_positive"}
        viewer._apply_filters()
        assert len(viewer.visible_rows) == 1
        assert viewer.visible_rows[0].index == 0

    def test_finding_row_has_repo_and_pr(self, report: ReviewReport) -> None:
        rows = _flatten_findings(report)
        assert rows[0].repo == "acme/app"
        assert rows[0].pr_number == 42

    def test_finding_row_no_pr_url(self) -> None:
        report = _make_report(pr_url=None)
        rows = _flatten_findings(report)
        assert rows[0].repo is None
        assert rows[0].pr_number is None


# ---------------------------------------------------------------------------
# cmd_findings entry point
# ---------------------------------------------------------------------------


class TestCmdFindings:
    def test_no_report_shows_error(self) -> None:
        session = MagicMock()
        session.last_review_report = None
        with patch(
            "code_review_agent.interactive.commands.findings_cmd.Console"
        ) as mock_console_cls:
            mock_console = MagicMock()
            mock_console_cls.return_value = mock_console
            cmd_findings([], session)

        mock_console.print.assert_called_once()
        call_args = mock_console.print.call_args[0][0]
        assert "No review available" in call_args

    def test_loads_from_storage(self, report: ReviewReport) -> None:
        session = MagicMock()
        session.last_review_report = None
        session.effective_settings.history_db_path = "~/.cra/reviews.db"

        mock_storage = MagicMock()
        mock_storage.get_review.return_value = {
            "report_json": report.model_dump_json(),
        }

        with (
            patch(
                "code_review_agent.storage.ReviewStorage",
                return_value=mock_storage,
            ),
            patch(
                "code_review_agent.interactive.commands.findings_cmd.Application"
            ) as mock_app_cls,
            patch("code_review_agent.interactive.commands.findings_cmd.Console"),
        ):
            mock_app = MagicMock()
            mock_app_cls.return_value = mock_app
            cmd_findings(["1"], session)

        mock_storage.get_review.assert_called_once_with(1)
        mock_app.run.assert_called_once()
