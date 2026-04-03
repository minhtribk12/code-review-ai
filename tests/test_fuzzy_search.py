"""Tests for fuzzy search across findings."""

from __future__ import annotations

from code_review_agent.interactive.commands.findings.models import FindingRow
from code_review_agent.interactive.fuzzy_search import fuzzy_search
from code_review_agent.models import Confidence, Severity


def _make_row(title: str, file_path: str = "app.py", agent: str = "security") -> FindingRow:
    return FindingRow(
        index=0,
        title=title,
        description=f"Description for {title}",
        file_path=file_path,
        agent_name=agent,
        severity=Severity.HIGH,
        confidence=Confidence.HIGH,
    )


class TestFuzzySearch:
    """Test fuzzy search functionality."""

    def test_empty_query(self) -> None:
        rows = [_make_row("test")]
        assert fuzzy_search(rows, "") == []

    def test_empty_rows(self) -> None:
        assert fuzzy_search([], "query") == []

    def test_exact_title_match(self) -> None:
        rows = [_make_row("SQL injection"), _make_row("XSS vulnerability")]
        results = fuzzy_search(rows, "SQL injection")
        assert len(results) >= 1
        assert results[0].row.title == "SQL injection"
        assert results[0].score > 0

    def test_partial_match(self) -> None:
        rows = [_make_row("SQL injection"), _make_row("unused import")]
        results = fuzzy_search(rows, "SQL")
        assert len(results) >= 1
        assert results[0].row.title == "SQL injection"

    def test_file_path_match(self) -> None:
        rows = [
            _make_row("bug1", file_path="src/db.py"),
            _make_row("bug2", file_path="src/api.py"),
        ]
        results = fuzzy_search(rows, "db.py")
        assert len(results) == 1
        assert results[0].matched_field == "file_path"

    def test_agent_name_match(self) -> None:
        rows = [
            _make_row("bug1", agent="security"),
            _make_row("bug2", agent="performance"),
        ]
        results = fuzzy_search(rows, "performance")
        assert len(results) >= 1
        assert results[0].row.agent_name == "performance"

    def test_case_insensitive(self) -> None:
        rows = [_make_row("SQL Injection")]
        results = fuzzy_search(rows, "sql injection")
        assert len(results) == 1

    def test_sorted_by_score(self) -> None:
        rows = [
            _make_row("sql helper"),  # partial match
            _make_row("sql"),  # exact match (higher score)
        ]
        results = fuzzy_search(rows, "sql")
        assert len(results) == 2
        assert results[0].score >= results[1].score

    def test_no_match(self) -> None:
        rows = [_make_row("SQL injection")]
        results = fuzzy_search(rows, "zzzzz")
        assert results == []

    def test_description_match(self) -> None:
        rows = [_make_row("bug", file_path="x.py")]
        results = fuzzy_search(rows, "Description")
        assert len(results) == 1
        assert results[0].matched_field == "description"
