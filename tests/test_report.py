from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from code_review_agent.models import (
    AgentResult,
    ReviewReport,
)
from code_review_agent.report import render_report_markdown, save_report

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
