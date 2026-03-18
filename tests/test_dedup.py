from __future__ import annotations

from code_review_agent.dedup import (
    DedupStrategy,
    _is_duplicate,
    _pick_survivor,
    _title_similarity,
    deduplicate_agent_results,
)
from code_review_agent.models import AgentResult, Finding


def _f(
    title: str = "Issue",
    severity: str = "medium",
    file_path: str | None = "app.py",
    line_number: int | None = 10,
) -> Finding:
    """Shorthand for creating a Finding."""
    return Finding(
        severity=severity,
        category="test",
        title=title,
        description="desc",
        file_path=file_path,
        line_number=line_number,
    )


def _r(agent_name: str, findings: list[Finding]) -> AgentResult:
    """Shorthand for creating an AgentResult."""
    return AgentResult(
        agent_name=agent_name,
        findings=findings,
        summary="summary",
        execution_time_seconds=1.0,
    )


# ---------------------------------------------------------------------------
# _is_duplicate
# ---------------------------------------------------------------------------


class TestIsDuplicate:
    """Test duplicate detection under each strategy."""

    def test_exact_match(self) -> None:
        a = _f(title="SQL injection", file_path="x.py", line_number=5)
        b = _f(title="SQL injection", file_path="x.py", line_number=5)
        assert _is_duplicate(a, b, DedupStrategy.EXACT) is True

    def test_exact_different_title(self) -> None:
        a = _f(title="SQL injection", file_path="x.py", line_number=5)
        b = _f(title="SQL injection risk", file_path="x.py", line_number=5)
        assert _is_duplicate(a, b, DedupStrategy.EXACT) is False

    def test_exact_different_line(self) -> None:
        a = _f(title="SQL injection", file_path="x.py", line_number=5)
        b = _f(title="SQL injection", file_path="x.py", line_number=6)
        assert _is_duplicate(a, b, DedupStrategy.EXACT) is False

    def test_exact_different_file(self) -> None:
        a = _f(title="SQL injection", file_path="x.py", line_number=5)
        b = _f(title="SQL injection", file_path="y.py", line_number=5)
        assert _is_duplicate(a, b, DedupStrategy.EXACT) is False

    def test_location_same_line(self) -> None:
        a = _f(title="SQL injection", file_path="x.py", line_number=5)
        b = _f(title="Different title", file_path="x.py", line_number=5)
        assert _is_duplicate(a, b, DedupStrategy.LOCATION) is True

    def test_location_different_line(self) -> None:
        a = _f(title="SQL injection", file_path="x.py", line_number=5)
        b = _f(title="SQL injection", file_path="x.py", line_number=6)
        assert _is_duplicate(a, b, DedupStrategy.LOCATION) is False

    def test_similar_above_threshold(self) -> None:
        a = _f(title="SQL injection vulnerability", file_path="x.py", line_number=5)
        b = _f(title="SQL injection risk", file_path="x.py", line_number=5)
        assert _is_duplicate(a, b, DedupStrategy.SIMILAR) is True

    def test_similar_below_threshold(self) -> None:
        a = _f(title="SQL injection", file_path="x.py", line_number=5)
        b = _f(title="Missing test coverage", file_path="x.py", line_number=5)
        assert _is_duplicate(a, b, DedupStrategy.SIMILAR) is False

    def test_similar_different_location(self) -> None:
        a = _f(title="SQL injection", file_path="x.py", line_number=5)
        b = _f(title="SQL injection", file_path="x.py", line_number=99)
        assert _is_duplicate(a, b, DedupStrategy.SIMILAR) is False

    def test_disabled_never_matches(self) -> None:
        a = _f(title="SQL injection", file_path="x.py", line_number=5)
        b = _f(title="SQL injection", file_path="x.py", line_number=5)
        assert _is_duplicate(a, b, DedupStrategy.DISABLED) is False

    def test_none_file_path_matches(self) -> None:
        a = _f(title="General issue", file_path=None, line_number=None)
        b = _f(title="General issue", file_path=None, line_number=None)
        assert _is_duplicate(a, b, DedupStrategy.EXACT) is True


# ---------------------------------------------------------------------------
# _title_similarity
# ---------------------------------------------------------------------------


class TestTitleSimilarity:
    """Test title similarity computation."""

    def test_identical(self) -> None:
        assert _title_similarity("SQL injection", "SQL injection") == 1.0

    def test_case_insensitive(self) -> None:
        assert _title_similarity("SQL Injection", "sql injection") == 1.0

    def test_different(self) -> None:
        assert _title_similarity("SQL injection", "Missing tests") < 0.5


# ---------------------------------------------------------------------------
# _pick_survivor
# ---------------------------------------------------------------------------


class TestPickSurvivor:
    """Test survival priority logic."""

    def test_higher_severity_wins(self) -> None:
        a = _f(severity="high")
        b = _f(severity="low")
        assert _pick_survivor(a, "style", b, "security") == 0  # a wins (HIGH > LOW)

    def test_same_severity_agent_priority_wins(self) -> None:
        a = _f(severity="medium")
        b = _f(severity="medium")
        assert _pick_survivor(a, "security", b, "style") == 0  # security > style

    def test_same_severity_style_loses_to_performance(self) -> None:
        a = _f(severity="medium")
        b = _f(severity="medium")
        assert _pick_survivor(a, "style", b, "performance") == 1  # performance > style


# ---------------------------------------------------------------------------
# deduplicate_agent_results
# ---------------------------------------------------------------------------


class TestDeduplicateAgentResults:
    """Test full dedup pipeline."""

    def test_no_duplicates_unchanged(self) -> None:
        results = [
            _r("security", [_f(title="Issue A", file_path="a.py", line_number=1)]),
            _r("style", [_f(title="Issue B", file_path="b.py", line_number=2)]),
        ]
        deduped = deduplicate_agent_results(results, DedupStrategy.EXACT)
        total = sum(len(r.findings) for r in deduped)
        assert total == 2

    def test_exact_duplicate_removed(self) -> None:
        finding = _f(title="SQL injection", file_path="x.py", line_number=5)
        results = [
            _r("security", [finding]),
            _r("style", [finding]),
        ]
        deduped = deduplicate_agent_results(results, DedupStrategy.EXACT)
        total = sum(len(r.findings) for r in deduped)
        assert total == 1

    def test_higher_severity_survives(self) -> None:
        low = _f(title="SQL injection", severity="low", file_path="x.py", line_number=5)
        high = _f(title="SQL injection", severity="high", file_path="x.py", line_number=5)
        results = [_r("style", [low]), _r("security", [high])]
        deduped = deduplicate_agent_results(results, DedupStrategy.EXACT)
        # Security's HIGH finding should survive
        security_result = next(r for r in deduped if r.agent_name == "security")
        style_result = next(r for r in deduped if r.agent_name == "style")
        assert len(security_result.findings) == 1
        assert len(style_result.findings) == 0

    def test_disabled_strategy_no_change(self) -> None:
        finding = _f(title="Same", file_path="x.py", line_number=5)
        results = [
            _r("security", [finding]),
            _r("style", [finding]),
        ]
        deduped = deduplicate_agent_results(results, DedupStrategy.DISABLED)
        total = sum(len(r.findings) for r in deduped)
        assert total == 2

    def test_location_strategy_merges_different_titles(self) -> None:
        a = _f(title="SQL injection", file_path="x.py", line_number=5)
        b = _f(title="Unparameterized query", file_path="x.py", line_number=5)
        results = [_r("security", [a]), _r("performance", [b])]
        deduped = deduplicate_agent_results(results, DedupStrategy.LOCATION)
        total = sum(len(r.findings) for r in deduped)
        assert total == 1

    def test_empty_findings(self) -> None:
        results = [
            _r("security", []),
            _r("style", []),
        ]
        deduped = deduplicate_agent_results(results, DedupStrategy.EXACT)
        total = sum(len(r.findings) for r in deduped)
        assert total == 0

    def test_preserves_agent_metadata(self) -> None:
        results = [
            _r("security", [_f(title="Issue", file_path="x.py", line_number=1)]),
        ]
        deduped = deduplicate_agent_results(results, DedupStrategy.EXACT)
        assert deduped[0].agent_name == "security"
        assert deduped[0].summary == "summary"
        assert deduped[0].execution_time_seconds == 1.0

    def test_does_not_mutate_input(self) -> None:
        finding = _f(title="SQL injection", file_path="x.py", line_number=5)
        original = [
            _r("security", [finding]),
            _r("style", [finding]),
        ]
        original_count = sum(len(r.findings) for r in original)
        deduplicate_agent_results(original, DedupStrategy.EXACT)
        after_count = sum(len(r.findings) for r in original)
        assert after_count == original_count

    def test_three_agents_same_finding(self) -> None:
        finding = _f(title="SQL injection", severity="medium", file_path="x.py", line_number=5)
        high_finding = _f(title="SQL injection", severity="high", file_path="x.py", line_number=5)
        results = [
            _r("security", [high_finding]),
            _r("performance", [finding]),
            _r("style", [finding]),
        ]
        deduped = deduplicate_agent_results(results, DedupStrategy.EXACT)
        total = sum(len(r.findings) for r in deduped)
        assert total == 1
        # Security's HIGH should survive
        security_result = next(r for r in deduped if r.agent_name == "security")
        assert len(security_result.findings) == 1
