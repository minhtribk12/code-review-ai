from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO
from typing import TYPE_CHECKING

from rich.console import Console

from code_review_agent.models import (
    AgentResult,
    Finding,
    ReviewReport,
)
from code_review_agent.report import render_report_markdown, render_report_rich, save_report

if TYPE_CHECKING:
    from pathlib import Path


class TestRenderReportMarkdown:
    """Test markdown report rendering."""

    def test_contains_all_sections(self, sample_review_report: ReviewReport) -> None:
        md = render_report_markdown(sample_review_report)

        assert "# Code Review Report" in md
        assert "https://github.com/acme/webapp/pull/42" in md
        assert "Summary" in md or "summary" in md.lower()
        assert "security" in md.lower()
        assert "performance" in md.lower()

    def test_includes_finding_details(self, sample_review_report: ReviewReport) -> None:
        md = render_report_markdown(sample_review_report)

        assert "SQL injection vulnerability fixed" in md
        assert "src/auth/login.py" in md

    def test_handles_empty_findings(self) -> None:
        report = ReviewReport(
            pr_url="https://github.com/org/repo/pull/1",
            reviewed_at=datetime.now(tz=UTC),
            agent_results=[
                AgentResult(
                    agent_name="security",
                    findings=[],
                    summary="No issues.",
                    execution_time_seconds=0.5,
                ),
            ],
            overall_summary="Clean code.",
            risk_level="low",
        )

        md = render_report_markdown(report)
        assert "# Code Review Report" in md

    def test_includes_risk_level(self, sample_review_report: ReviewReport) -> None:
        md = render_report_markdown(sample_review_report)
        assert "HIGH" in md or "high" in md.lower()

    def test_output_is_valid_markdown_string(self, sample_review_report: ReviewReport) -> None:
        md = render_report_markdown(sample_review_report)
        assert isinstance(md, str)
        assert len(md) > 0
        assert md.startswith("#")


# ---------------------------------------------------------------------------
# Rich terminal rendering (CRA-24)
# ---------------------------------------------------------------------------


def _capture_rich(report: ReviewReport) -> str:
    """Render report to a string by capturing Rich console output."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=120)

    from unittest.mock import patch

    with patch("code_review_agent.report.Console", return_value=console):
        render_report_rich(report)

    return buf.getvalue()


class TestRenderReportRich:
    """Test Rich terminal report rendering."""

    def test_contains_report_title(self, sample_review_report: ReviewReport) -> None:
        output = _capture_rich(sample_review_report)
        assert "Code Review Report" in output

    def test_contains_risk_level(self, sample_review_report: ReviewReport) -> None:
        output = _capture_rich(sample_review_report)
        assert "HIGH" in output

    def test_contains_pr_url(self, sample_review_report: ReviewReport) -> None:
        output = _capture_rich(sample_review_report)
        assert "github.com/acme/webapp/pull/42" in output

    def test_contains_overall_summary(self, sample_review_report: ReviewReport) -> None:
        output = _capture_rich(sample_review_report)
        assert "Overall Summary" in output

    def test_contains_agent_names(self, sample_review_report: ReviewReport) -> None:
        output = _capture_rich(sample_review_report)
        assert "SECURITY" in output
        assert "PERFORMANCE" in output

    def test_contains_finding_details(self, sample_review_report: ReviewReport) -> None:
        output = _capture_rich(sample_review_report)
        assert "SQL injection" in output

    def test_contains_severity_counts(self, sample_review_report: ReviewReport) -> None:
        output = _capture_rich(sample_review_report)
        assert "critical" in output.lower()
        assert "high" in output.lower()

    def test_empty_findings_shows_clean_message(self) -> None:
        report = ReviewReport(
            reviewed_at=datetime.now(tz=UTC),
            agent_results=[
                AgentResult(
                    agent_name="security",
                    findings=[],
                    summary="No issues.",
                    execution_time_seconds=0.5,
                ),
            ],
            overall_summary="Clean.",
            risk_level="low",
        )
        output = _capture_rich(report)
        assert "looks good" in output.lower()

    def test_findings_table_present_when_findings_exist(
        self, sample_review_report: ReviewReport
    ) -> None:
        output = _capture_rich(sample_review_report)
        assert "All Findings" in output

    def test_no_pr_url_still_renders(self) -> None:
        report = ReviewReport(
            reviewed_at=datetime.now(tz=UTC),
            agent_results=[
                AgentResult(
                    agent_name="security",
                    findings=[
                        Finding(
                            severity="medium",
                            category="test",
                            title="Test finding",
                            description="Desc.",
                        ),
                    ],
                    summary="Found 1 issue.",
                    execution_time_seconds=1.0,
                ),
            ],
            overall_summary="Minor issues.",
            risk_level="medium",
        )
        output = _capture_rich(report)
        assert "Code Review Report" in output
        assert "PR:" not in output


class TestSaveReport:
    """Test writing report to disk."""

    def test_writes_markdown_file(
        self, tmp_path: Path, sample_review_report: ReviewReport
    ) -> None:
        output_file = tmp_path / "review_report.md"
        save_report(sample_review_report, output_file)

        assert output_file.exists()
        content = output_file.read_text()
        assert "# Code Review Report" in content

    def test_creates_parent_directories(
        self, tmp_path: Path, sample_review_report: ReviewReport
    ) -> None:
        output_file = tmp_path / "nested" / "dir" / "report.md"
        save_report(sample_review_report, output_file)
        assert output_file.exists()

    def test_overwrites_existing_file(
        self, tmp_path: Path, sample_review_report: ReviewReport
    ) -> None:
        output_file = tmp_path / "report.md"
        output_file.write_text("old content")

        save_report(sample_review_report, output_file)

        content = output_file.read_text()
        assert "old content" not in content
        assert "# Code Review Report" in content
