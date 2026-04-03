"""Tests for review comparison."""

from __future__ import annotations

from unittest.mock import MagicMock

from code_review_agent.review_diff import (
    FindingStatus,
    ReviewComparison,
    compare_reviews,
    format_comparison,
)


def _make_finding(
    title: str,
    file_path: str = "app.py",
    line_number: int = 10,
    severity: str = "high",
    agent_name: str = "security",
) -> dict[str, object]:
    return {
        "title": title,
        "file_path": file_path,
        "line_number": line_number,
        "severity": severity,
        "agent_name": agent_name,
    }


class TestCompareReviews:
    """Test comparing two review runs."""

    def test_new_finding(self) -> None:
        storage = MagicMock()
        storage.load_findings_for_review.side_effect = [
            [],  # old: empty
            [_make_finding("SQL injection")],  # new: one finding
        ]
        result = compare_reviews(storage, 1, 2)
        assert result.new_count == 1
        assert result.resolved_count == 0
        assert result.persistent_count == 0

    def test_resolved_finding(self) -> None:
        storage = MagicMock()
        storage.load_findings_for_review.side_effect = [
            [_make_finding("SQL injection")],  # old: one finding
            [],  # new: empty
        ]
        result = compare_reviews(storage, 1, 2)
        assert result.resolved_count == 1
        assert result.new_count == 0

    def test_persistent_finding(self) -> None:
        storage = MagicMock()
        finding = _make_finding("SQL injection")
        storage.load_findings_for_review.side_effect = [
            [finding],  # old
            [finding],  # new (same)
        ]
        result = compare_reviews(storage, 1, 2)
        assert result.persistent_count == 1
        assert result.resolved_count == 0
        assert result.new_count == 0

    def test_mixed_changes(self) -> None:
        storage = MagicMock()
        storage.load_findings_for_review.side_effect = [
            [
                _make_finding("Old bug", line_number=1),
                _make_finding("Persistent", line_number=2),
            ],
            [
                _make_finding("Persistent", line_number=2),
                _make_finding("New issue", line_number=3),
            ],
        ]
        result = compare_reviews(storage, 1, 2)
        assert result.resolved_count == 1
        assert result.new_count == 1
        assert result.persistent_count == 1

    def test_comparison_is_frozen(self) -> None:
        import pytest

        comp = ReviewComparison(
            old_review_id=1,
            new_review_id=2,
            resolved=(),
            new_findings=(),
            persistent=(),
        )
        with pytest.raises(AttributeError):
            comp.old_review_id = 3  # type: ignore[misc]


class TestFormatComparison:
    """Test comparison report formatting."""

    def test_formats_counts(self) -> None:
        comp = ReviewComparison(
            old_review_id=1,
            new_review_id=2,
            resolved=(),
            new_findings=(),
            persistent=(),
        )
        report = format_comparison(comp)
        assert "#1 vs #2" in report
        assert "0 resolved" in report

    def test_shows_sections(self) -> None:
        from code_review_agent.review_diff import ComparedFinding

        comp = ReviewComparison(
            old_review_id=1,
            new_review_id=2,
            resolved=(
                ComparedFinding("old bug", "a.py", 1, "high", "sec", FindingStatus.RESOLVED),
            ),
            new_findings=(
                ComparedFinding("new bug", "b.py", 2, "medium", "perf", FindingStatus.NEW),
            ),
            persistent=(),
        )
        report = format_comparison(comp)
        assert "[RESOLVED]" in report
        assert "[NEW]" in report
        assert "old bug" in report
        assert "new bug" in report
