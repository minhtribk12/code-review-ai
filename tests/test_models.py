from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from code_review_agent.models import (
    AgentResult,
    DiffFile,
    Finding,
    ReviewInput,
    ReviewReport,
)

SEVERITIES = ["critical", "high", "medium", "low"]


class TestFinding:
    """Test Finding model creation, validation, and serialization."""

    @pytest.mark.parametrize("severity", SEVERITIES)
    def test_creation_with_all_severity_levels(self, severity: str) -> None:
        finding = Finding(
            severity=severity,
            category="test",
            title=f"Finding at {severity}",
            description="Test description.",
            file_path="app.py",
            line_number=1,
            suggestion="Test suggestion.",
        )
        assert finding.severity == severity
        assert finding.file_path == "app.py"

    def test_optional_fields_default_to_none(self) -> None:
        finding = Finding(
            severity="low",
            category="test",
            title="Minimal finding",
            description="Desc.",
        )
        assert finding.file_path is None
        assert finding.line_number is None
        assert finding.suggestion is None

    def test_immutability(self, sample_finding: Finding) -> None:
        with pytest.raises(ValidationError):
            sample_finding.title = "mutated"  # type: ignore[misc]

    def test_serialization_to_dict(self, sample_finding: Finding) -> None:
        data = sample_finding.model_dump()
        assert isinstance(data, dict)
        assert data["file_path"] == "src/auth/login.py"
        assert data["severity"] == "high"
        assert "title" in data
        assert "description" in data

    def test_serialization_to_json(self, sample_finding: Finding) -> None:
        raw = sample_finding.model_dump_json()
        parsed = json.loads(raw)
        assert parsed["category"] == "security"
        assert parsed["line_number"] == 12

    def test_required_fields_enforced(self) -> None:
        with pytest.raises(ValidationError):
            Finding()  # type: ignore[call-arg]

    def test_invalid_severity_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Finding(
                severity="extreme",  # type: ignore[arg-type]
                category="test",
                title="Bad severity",
                description="Desc.",
            )


class TestDiffFile:
    """Test DiffFile model creation and immutability."""

    def test_creation(self) -> None:
        diff = DiffFile(
            filename="main.py",
            patch="@@ -1,3 +1,5 @@\n+import os\n",
            status="modified",
        )
        assert diff.filename == "main.py"
        assert diff.status == "modified"

    def test_immutability(self) -> None:
        diff = DiffFile(filename="main.py", patch="@@ some patch", status="added")
        with pytest.raises(ValidationError):
            diff.filename = "other.py"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        diff = DiffFile(
            filename="utils.py",
            patch="@@ -0,0 +1 @@\n+pass\n",
            status="added",
        )
        data = json.loads(diff.model_dump_json())
        restored = DiffFile.model_validate(data)
        assert restored == diff


class TestReviewInput:
    """Test ReviewInput model."""

    def test_creation(self, sample_review_input: ReviewInput) -> None:
        assert sample_review_input.pr_url == "https://github.com/acme/webapp/pull/42"
        assert len(sample_review_input.diff_files) == 2

    def test_immutability(self, sample_review_input: ReviewInput) -> None:
        with pytest.raises(ValidationError):
            sample_review_input.pr_url = "changed"  # type: ignore[misc]


class TestAgentResult:
    """Test AgentResult model."""

    def test_successful_result(self, sample_agent_result: AgentResult) -> None:
        assert sample_agent_result.agent_name == "security"
        assert len(sample_agent_result.findings) == 2
        assert sample_agent_result.summary != ""
        assert sample_agent_result.execution_time_seconds > 0

    def test_empty_findings(self) -> None:
        result = AgentResult(
            agent_name="performance",
            findings=[],
            summary="No issues found.",
            execution_time_seconds=0.5,
        )
        assert len(result.findings) == 0

    def test_immutability(self, sample_agent_result: AgentResult) -> None:
        with pytest.raises(ValidationError):
            sample_agent_result.agent_name = "hacked"  # type: ignore[misc]


class TestReviewReport:
    """Test ReviewReport model and computed properties."""

    def test_creation(self, sample_review_report: ReviewReport) -> None:
        assert sample_review_report.pr_url == "https://github.com/acme/webapp/pull/42"
        assert len(sample_review_report.agent_results) == 4

    def test_total_findings_counts_by_severity(self, sample_review_report: ReviewReport) -> None:
        totals = sample_review_report.total_findings
        # security: 1 high + 1 low, performance: 1 medium, style: 0, test_coverage: 1 high
        assert totals["critical"] == 0
        assert totals["high"] == 2
        assert totals["medium"] == 1
        assert totals["low"] == 1

    def test_empty_report(self) -> None:
        report = ReviewReport(
            reviewed_at=datetime.now(tz=UTC),
            agent_results=[],
            overall_summary="No agents ran.",
            risk_level="low",
        )
        totals = report.total_findings
        assert all(count == 0 for count in totals.values())

    def test_immutability(self, sample_review_report: ReviewReport) -> None:
        with pytest.raises(ValidationError):
            sample_review_report.pr_url = "https://evil.com"  # type: ignore[misc]

    def test_serialization_to_json(self, sample_review_report: ReviewReport) -> None:
        raw = sample_review_report.model_dump_json()
        parsed = json.loads(raw)
        assert parsed["pr_url"] == "https://github.com/acme/webapp/pull/42"
        assert isinstance(parsed["agent_results"], list)
        assert len(parsed["agent_results"]) == 4
