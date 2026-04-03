"""Tests for linter integration."""

from __future__ import annotations

import json

from code_review_agent.linters import (
    LinterConfig,
    merge_linter_findings,
    run_linter,
)
from code_review_agent.models import Finding, Severity


class TestRunLinter:
    """Test linter execution."""

    def test_successful_run(self) -> None:
        config = LinterConfig(name="echo", command="echo '[]'", parser="ruff")
        result = run_linter(config)
        assert result.is_success
        assert result.findings == []

    def test_timeout(self) -> None:
        config = LinterConfig(name="slow", command="sleep 10", parser="generic", timeout_seconds=1)
        result = run_linter(config)
        assert not result.is_success
        assert "Timed out" in result.error_message


class TestParseRuff:
    """Test ruff JSON output parsing."""

    def test_parses_findings(self) -> None:
        from code_review_agent.linters import _parse_ruff

        output = json.dumps(
            [
                {
                    "code": "S101",
                    "message": "Use of assert detected",
                    "filename": "test.py",
                    "location": {"row": 10, "column": 1},
                }
            ]
        )
        findings = _parse_ruff(output, "ruff")
        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH  # S = security
        assert "S101" in findings[0].title
        assert findings[0].file_path == "test.py"
        assert findings[0].line_number == 10

    def test_invalid_json(self) -> None:
        from code_review_agent.linters import _parse_ruff

        assert _parse_ruff("not json", "ruff") == []


class TestParseEslint:
    """Test eslint JSON output parsing."""

    def test_parses_findings(self) -> None:
        from code_review_agent.linters import _parse_eslint

        output = json.dumps(
            [
                {
                    "filePath": "src/app.js",
                    "messages": [
                        {
                            "ruleId": "no-unused-vars",
                            "severity": 2,
                            "message": "'x' is defined but never used",
                            "line": 5,
                        }
                    ],
                }
            ]
        )
        findings = _parse_eslint(output, "eslint")
        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH
        assert findings[0].file_path == "src/app.js"


class TestParseMypy:
    """Test mypy JSON output parsing."""

    def test_parses_findings(self) -> None:
        from code_review_agent.linters import _parse_mypy

        output = json.dumps(
            {
                "severity": "error",
                "code": "arg-type",
                "message": "Argument 1 has incompatible type",
                "file": "app.py",
                "line": 42,
            }
        )
        findings = _parse_mypy(output, "mypy")
        assert len(findings) == 1
        assert findings[0].line_number == 42


class TestParseGeneric:
    """Test generic line-based output parsing."""

    def test_parses_colon_format(self) -> None:
        from code_review_agent.linters import _parse_generic

        output = "app.py:10: some warning here\napp.py:20: another issue\n"
        findings = _parse_generic(output, "custom")
        assert len(findings) == 2
        assert findings[0].file_path == "app.py"
        assert findings[0].line_number == 10


class TestMergeLinterFindings:
    """Test merging AI and linter findings."""

    def test_deduplicates_by_file_line(self) -> None:
        ai = [
            Finding(
                severity=Severity.HIGH,
                category="security",
                title="SQL injection",
                description="desc",
                file_path="db.py",
                line_number=10,
            )
        ]
        linter = [
            Finding(
                severity=Severity.MEDIUM,
                category="[ruff] S608",
                title="SQL injection pattern",
                description="desc",
                file_path="db.py",
                line_number=10,  # same location
            ),
            Finding(
                severity=Severity.LOW,
                category="[ruff] E501",
                title="Line too long",
                description="desc",
                file_path="db.py",
                line_number=20,  # different location
            ),
        ]
        merged = merge_linter_findings(ai, linter)
        assert len(merged) == 2  # AI finding + unique linter finding

    def test_empty_inputs(self) -> None:
        assert merge_linter_findings([], []) == []
