from __future__ import annotations

from code_review_agent.models import AgentResult, Finding, Severity
from code_review_agent.orchestrator import _SEVERITY_ORDER, Orchestrator


def _f(severity: str = "medium") -> Finding:
    """Shorthand for creating a Finding."""
    return Finding(
        severity=severity,
        category="test",
        title="Test finding",
        description="desc",
    )


def _r(agent_name: str, findings: list[Finding]) -> AgentResult:
    """Shorthand for creating an AgentResult."""
    return AgentResult(
        agent_name=agent_name,
        findings=findings,
        summary="summary",
        execution_time_seconds=1.0,
    )


def _make_orchestrator() -> Orchestrator:
    """Create a minimal Orchestrator for testing validation."""
    orch = Orchestrator.__new__(Orchestrator)
    return orch


# ---------------------------------------------------------------------------
# _validate_risk_level
# ---------------------------------------------------------------------------


class TestValidateRiskLevel:
    """Test risk level validation logic."""

    def test_zero_findings_forces_low(self) -> None:
        orch = _make_orchestrator()
        result = orch._validate_risk_level(Severity.CRITICAL, [_r("security", [])])
        assert result == Severity.LOW

    def test_zero_findings_low_unchanged(self) -> None:
        orch = _make_orchestrator()
        result = orch._validate_risk_level(Severity.LOW, [_r("security", [])])
        assert result == Severity.LOW

    def test_proposed_matches_max_finding(self) -> None:
        orch = _make_orchestrator()
        results = [_r("security", [_f("high")])]
        result = orch._validate_risk_level(Severity.HIGH, results)
        assert result == Severity.HIGH

    def test_proposed_below_max_finding(self) -> None:
        orch = _make_orchestrator()
        results = [_r("security", [_f("high")])]
        result = orch._validate_risk_level(Severity.LOW, results)
        assert result == Severity.LOW  # LLM says LOW, that's fine

    def test_one_level_escalation_allowed(self) -> None:
        orch = _make_orchestrator()
        results = [_r("security", [_f("medium")])]
        # Max is MEDIUM, proposed HIGH is +1 level -> allowed
        result = orch._validate_risk_level(Severity.HIGH, results)
        assert result == Severity.HIGH

    def test_two_level_escalation_overridden(self) -> None:
        orch = _make_orchestrator()
        results = [_r("security", [_f("low")])]
        # Max is LOW, proposed HIGH is +2 levels -> override to MEDIUM (LOW+1)
        result = orch._validate_risk_level(Severity.HIGH, results)
        assert result == Severity.MEDIUM

    def test_critical_proposed_with_low_findings_overridden(self) -> None:
        orch = _make_orchestrator()
        results = [_r("security", [_f("low")])]
        # Max is LOW, proposed CRITICAL is +3 levels -> override to MEDIUM
        result = orch._validate_risk_level(Severity.CRITICAL, results)
        assert result == Severity.MEDIUM

    def test_critical_with_high_findings_allowed(self) -> None:
        orch = _make_orchestrator()
        results = [_r("security", [_f("high")])]
        # Max is HIGH, proposed CRITICAL is +1 level -> allowed
        result = orch._validate_risk_level(Severity.CRITICAL, results)
        assert result == Severity.CRITICAL

    def test_critical_with_critical_findings_unchanged(self) -> None:
        orch = _make_orchestrator()
        results = [_r("security", [_f("critical")])]
        result = orch._validate_risk_level(Severity.CRITICAL, results)
        assert result == Severity.CRITICAL

    def test_multiple_agents_uses_max_across_all(self) -> None:
        orch = _make_orchestrator()
        results = [
            _r("security", [_f("medium")]),
            _r("style", [_f("low")]),
        ]
        # Max across all agents is MEDIUM, proposed HIGH is +1 -> allowed
        result = orch._validate_risk_level(Severity.HIGH, results)
        assert result == Severity.HIGH

    def test_mixed_findings_escalation(self) -> None:
        orch = _make_orchestrator()
        results = [
            _r("security", [_f("medium"), _f("low")]),
            _r("style", [_f("low")]),
        ]
        # Max is MEDIUM, proposed CRITICAL is +2 -> override to HIGH
        result = orch._validate_risk_level(Severity.CRITICAL, results)
        assert result == Severity.HIGH


# ---------------------------------------------------------------------------
# Severity order
# ---------------------------------------------------------------------------


class TestSeverityOrder:
    """Verify severity ordering constant."""

    def test_order_low_to_critical(self) -> None:
        assert _SEVERITY_ORDER == [
            Severity.LOW,
            Severity.MEDIUM,
            Severity.HIGH,
            Severity.CRITICAL,
        ]

    def test_low_is_index_zero(self) -> None:
        assert _SEVERITY_ORDER.index(Severity.LOW) == 0

    def test_critical_is_highest(self) -> None:
        assert _SEVERITY_ORDER.index(Severity.CRITICAL) == 3
