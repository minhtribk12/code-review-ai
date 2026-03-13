from __future__ import annotations

from unittest.mock import MagicMock, patch

from code_review_agent.models import (
    AgentResult,
    Finding,
    ReviewInput,
    ReviewReport,
    SynthesisResponse,
)
from code_review_agent.orchestrator import Orchestrator


def _make_agent_result(agent_name: str) -> AgentResult:
    """Create a minimal successful AgentResult for a given agent."""
    return AgentResult(
        agent_name=agent_name,
        findings=[
            Finding(
                file_path="src/app.py",
                line_number=1,
                severity="medium",
                category=agent_name,
                title=f"{agent_name} finding",
                description=f"A mock finding from the {agent_name} agent.",
                suggestion="No action needed.",
            ),
        ],
        summary=f"{agent_name} completed successfully.",
        execution_time_seconds=1.0,
    )


class TestOrchestratorRunsAgents:
    """Verify that the orchestrator dispatches work to every agent."""

    def test_report_contains_all_agent_results(
        self,
        sample_review_input: ReviewInput,
        mock_settings: MagicMock,
        mock_llm_client: MagicMock,
        mock_synthesis_response: SynthesisResponse,
    ) -> None:
        mock_llm_client.complete.return_value = mock_synthesis_response
        orchestrator = Orchestrator(settings=mock_settings, llm_client=mock_llm_client)

        agent_names = ["security", "performance", "style", "test_coverage"]
        results = [_make_agent_result(name) for name in agent_names]

        with patch.object(orchestrator, "_run_agents", return_value=results):
            report = orchestrator.run(review_input=sample_review_input)

        assert isinstance(report, ReviewReport)
        result_names = {r.agent_name for r in report.agent_results}
        assert result_names == set(agent_names)


class TestOrchestratorGracefulDegradation:
    """Verify partial results when agents fail."""

    def test_failed_agent_excluded_from_results(
        self,
        sample_review_input: ReviewInput,
        mock_settings: MagicMock,
        mock_llm_client: MagicMock,
        mock_synthesis_response: SynthesisResponse,
    ) -> None:
        mock_llm_client.complete.return_value = mock_synthesis_response
        orchestrator = Orchestrator(settings=mock_settings, llm_client=mock_llm_client)

        # Only 3 agents succeed, security is missing
        partial_results = [
            _make_agent_result(name) for name in ["performance", "style", "test_coverage"]
        ]

        with patch.object(orchestrator, "_run_agents", return_value=partial_results):
            report = orchestrator.run(review_input=sample_review_input)

        assert isinstance(report, ReviewReport)
        result_names = {r.agent_name for r in report.agent_results}
        assert "security" not in result_names
        assert len(report.agent_results) == 3

    def test_all_agents_fail_returns_empty_report(
        self,
        sample_review_input: ReviewInput,
        mock_settings: MagicMock,
        mock_llm_client: MagicMock,
        mock_synthesis_response: SynthesisResponse,
    ) -> None:
        mock_llm_client.complete.return_value = mock_synthesis_response
        orchestrator = Orchestrator(settings=mock_settings, llm_client=mock_llm_client)

        with patch.object(orchestrator, "_run_agents", return_value=[]):
            report = orchestrator.run(review_input=sample_review_input)

        assert isinstance(report, ReviewReport)
        totals = report.total_findings
        assert all(count == 0 for count in totals.values())


class TestOrchestratorReportAssembly:
    """Verify the ReviewReport is properly assembled."""

    def test_report_has_correct_pr_metadata(
        self,
        sample_review_input: ReviewInput,
        mock_settings: MagicMock,
        mock_llm_client: MagicMock,
        mock_synthesis_response: SynthesisResponse,
    ) -> None:
        mock_llm_client.complete.return_value = mock_synthesis_response
        orchestrator = Orchestrator(settings=mock_settings, llm_client=mock_llm_client)

        with patch.object(
            orchestrator, "_run_agents", return_value=[_make_agent_result("security")]
        ):
            report = orchestrator.run(review_input=sample_review_input)

        assert report.pr_url == sample_review_input.pr_url

    def test_findings_collected_across_agents(
        self,
        sample_review_input: ReviewInput,
        mock_settings: MagicMock,
        mock_llm_client: MagicMock,
        mock_synthesis_response: SynthesisResponse,
    ) -> None:
        mock_llm_client.complete.return_value = mock_synthesis_response
        security_result = AgentResult(
            agent_name="security",
            findings=[
                Finding(
                    file_path="a.py",
                    line_number=1,
                    severity="critical",
                    category="security",
                    title="Critical vuln",
                    description="Desc.",
                    suggestion="Fix it.",
                ),
                Finding(
                    file_path="b.py",
                    line_number=5,
                    severity="high",
                    category="security",
                    title="High vuln",
                    description="Desc.",
                    suggestion="Fix it.",
                ),
            ],
            summary="Found 2 security issues.",
            execution_time_seconds=1.5,
        )
        other_results = [_make_agent_result("performance"), _make_agent_result("style")]

        orchestrator = Orchestrator(settings=mock_settings, llm_client=mock_llm_client)

        with patch.object(
            orchestrator,
            "_run_agents",
            return_value=[security_result, *other_results],
        ):
            report = orchestrator.run(review_input=sample_review_input)

        totals = report.total_findings
        assert totals["critical"] == 1
        assert totals["high"] == 1
        assert totals["medium"] == 2  # one from each of performance, style
