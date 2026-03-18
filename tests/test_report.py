from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO
from typing import TYPE_CHECKING

from rich.console import Console

from code_review_agent.models import (
    AgentResult,
    AgentStatus,
    Finding,
    OutputFormat,
    ReviewReport,
)
from code_review_agent.report import (
    render_report_json,
    render_report_markdown,
    render_report_rich,
    save_report,
)

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


# ---------------------------------------------------------------------------
# Failed agents in report (CRA-29)
# ---------------------------------------------------------------------------


def _make_report_with_failed_agent() -> ReviewReport:
    """Build a report with 1 successful + 1 failed agent."""
    return ReviewReport(
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
            AgentResult(
                agent_name="performance",
                findings=[],
                summary="",
                execution_time_seconds=0.5,
                status=AgentStatus.FAILED,
                error_message="LLM returned empty response",
            ),
        ],
        overall_summary="Partial review.",
        risk_level="medium",
    )


class TestFailedAgentsRich:
    """Test failed agent rendering in Rich output."""

    def test_header_shows_warning(self) -> None:
        report = _make_report_with_failed_agent()
        output = _capture_rich(report)
        assert "WARNING" in output
        assert "1 of 2 agents failed" in output

    def test_failed_agent_shows_error(self) -> None:
        report = _make_report_with_failed_agent()
        output = _capture_rich(report)
        assert "PERFORMANCE" in output
        assert "FAILED" in output
        assert "empty response" in output

    def test_successful_agent_shows_normally(self) -> None:
        report = _make_report_with_failed_agent()
        output = _capture_rich(report)
        assert "SECURITY" in output
        assert "findings" in output

    def test_no_failed_agents_no_warning(self, sample_review_report: ReviewReport) -> None:
        output = _capture_rich(sample_review_report)
        assert "WARNING" not in output
        assert "FAILED" not in output


class TestFailedAgentsMarkdown:
    """Test failed agent rendering in markdown output."""

    def test_header_shows_warning(self) -> None:
        report = _make_report_with_failed_agent()
        md = render_report_markdown(report)
        assert "WARNING" in md
        assert "1 of 2 agents failed" in md

    def test_failed_agent_section(self) -> None:
        report = _make_report_with_failed_agent()
        md = render_report_markdown(report)
        assert "Performance Agent (FAILED)" in md
        assert "empty response" in md

    def test_successful_agent_section(self) -> None:
        report = _make_report_with_failed_agent()
        md = render_report_markdown(report)
        assert "Security Agent" in md
        assert "1 findings" in md

    def test_no_failed_agents_no_warning(self, sample_review_report: ReviewReport) -> None:
        md = render_report_markdown(sample_review_report)
        assert "WARNING" not in md
        assert "FAILED" not in md


class TestRenderReportJson:
    """Test JSON report rendering."""

    def test_valid_json(self, sample_review_report: ReviewReport) -> None:
        import json

        json_str = render_report_json(sample_review_report)
        data = json.loads(json_str)
        assert isinstance(data, dict)

    def test_contains_risk_level(self, sample_review_report: ReviewReport) -> None:
        import json

        data = json.loads(render_report_json(sample_review_report))
        assert data["risk_level"] == "high"

    def test_contains_agent_results(self, sample_review_report: ReviewReport) -> None:
        import json

        data = json.loads(render_report_json(sample_review_report))
        assert len(data["agent_results"]) == 4

    def test_contains_total_findings(self, sample_review_report: ReviewReport) -> None:
        import json

        data = json.loads(render_report_json(sample_review_report))
        assert "total_findings" in data
        assert data["total_findings"]["high"] >= 1

    def test_contains_pr_url(self, sample_review_report: ReviewReport) -> None:
        import json

        data = json.loads(render_report_json(sample_review_report))
        assert data["pr_url"] == "https://github.com/acme/webapp/pull/42"


class TestSaveReportJson:
    """Test saving report in JSON format."""

    def test_save_as_json(self, sample_review_report: ReviewReport, tmp_path: Path) -> None:
        import json

        path = tmp_path / "report.json"
        save_report(sample_review_report, path, output_format=OutputFormat.JSON)
        data = json.loads(path.read_text())
        assert data["risk_level"] == "high"

    def test_save_as_markdown_default(
        self, sample_review_report: ReviewReport, tmp_path: Path
    ) -> None:
        path = tmp_path / "report.md"
        save_report(sample_review_report, path)
        content = path.read_text()
        assert content.startswith("# Code Review Report")
