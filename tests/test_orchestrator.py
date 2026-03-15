from __future__ import annotations

from unittest.mock import MagicMock, call, patch

from code_review_agent.models import (
    AgentResult,
    Finding,
    ReviewEvent,
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


class TestOrchestratorEvents:
    """Verify that the orchestrator emits review events to the callback."""

    def test_emits_agent_events(
        self,
        sample_review_input: ReviewInput,
        mock_settings: MagicMock,
        mock_llm_client: MagicMock,
        mock_synthesis_response: SynthesisResponse,
    ) -> None:
        mock_llm_client.complete.return_value = mock_synthesis_response
        event_callback = MagicMock()
        orchestrator = Orchestrator(
            settings=mock_settings,
            llm_client=mock_llm_client,
            on_event=event_callback,
        )

        agent_names = ["security", "performance"]
        results = [_make_agent_result(name) for name in agent_names]

        with patch.object(orchestrator, "_run_agents", return_value=results):
            orchestrator.run(review_input=sample_review_input)

        # Synthesis events should fire for multi-agent runs
        synthesis_calls = [
            c
            for c in event_callback.call_args_list
            if c[0][0] in (ReviewEvent.SYNTHESIS_STARTED, ReviewEvent.SYNTHESIS_COMPLETED)
        ]
        assert len(synthesis_calls) == 2
        assert synthesis_calls[0] == call(ReviewEvent.SYNTHESIS_STARTED, "synthesis", None)
        assert synthesis_calls[1][0][0] == ReviewEvent.SYNTHESIS_COMPLETED

    def test_no_synthesis_events_for_single_agent(
        self,
        sample_review_input: ReviewInput,
        mock_settings: MagicMock,
        mock_llm_client: MagicMock,
    ) -> None:
        event_callback = MagicMock()
        orchestrator = Orchestrator(
            settings=mock_settings,
            llm_client=mock_llm_client,
            on_event=event_callback,
        )

        results = [_make_agent_result("security")]

        with patch.object(orchestrator, "_run_agents", return_value=results):
            orchestrator.run(review_input=sample_review_input)

        # No synthesis events for single-agent runs
        synthesis_calls = [
            c
            for c in event_callback.call_args_list
            if c[0][0] in (ReviewEvent.SYNTHESIS_STARTED, ReviewEvent.SYNTHESIS_COMPLETED)
        ]
        assert len(synthesis_calls) == 0

    def test_no_error_without_callback(
        self,
        sample_review_input: ReviewInput,
        mock_settings: MagicMock,
        mock_llm_client: MagicMock,
    ) -> None:
        """Orchestrator works fine without an event callback."""
        orchestrator = Orchestrator(
            settings=mock_settings,
            llm_client=mock_llm_client,
            on_event=None,
        )

        results = [_make_agent_result("security")]

        with patch.object(orchestrator, "_run_agents", return_value=results):
            report = orchestrator.run(review_input=sample_review_input)

        assert isinstance(report, ReviewReport)


class TestDeepeningLoop:
    """Test iterative deepening (multiple agent rounds)."""

    def test_single_round_default(
        self,
        sample_review_input: ReviewInput,
        mock_settings: MagicMock,
        mock_llm_client: MagicMock,
    ) -> None:
        """Default max_deepening_rounds=1 runs agents once."""
        orchestrator = Orchestrator(
            settings=mock_settings,
            llm_client=mock_llm_client,
        )
        results = [_make_agent_result("security")]

        with patch.object(orchestrator, "_run_agents", return_value=results) as mock_run:
            report = orchestrator.run(review_input=sample_review_input)

        assert mock_run.call_count == 1
        assert report.rounds_completed == 1

    def test_multiple_rounds_with_new_findings(
        self,
        sample_review_input: ReviewInput,
        mock_settings: MagicMock,
        mock_llm_client: MagicMock,
        mock_synthesis_response: SynthesisResponse,
    ) -> None:
        """Multiple rounds run when max_deepening_rounds > 1."""
        mock_settings.max_deepening_rounds = 3
        mock_llm_client.complete.return_value = mock_synthesis_response
        orchestrator = Orchestrator(
            settings=mock_settings,
            llm_client=mock_llm_client,
        )

        # Round 1: one finding
        round1 = [_make_agent_result("security")]
        # Round 2: a NEW finding (different file+line+title)
        round2_finding = Finding(
            file_path="src/new.py",
            line_number=99,
            severity="high",
            category="security",
            title="New round 2 finding",
            description="Found in deeper analysis.",
            suggestion="Fix.",
        )
        round2 = [
            AgentResult(
                agent_name="security",
                findings=[round2_finding],
                summary="Found deeper issue.",
                execution_time_seconds=1.0,
            ),
        ]
        # Round 3: no new findings (convergence)
        round3 = [
            AgentResult(
                agent_name="security",
                findings=[],
                summary="No new issues.",
                execution_time_seconds=0.5,
            ),
        ]

        call_count = 0

        def side_effect(**kwargs: object) -> list[AgentResult]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return round1
            if call_count == 2:
                return round2
            return round3

        with patch.object(orchestrator, "_run_agents", side_effect=side_effect):
            report = orchestrator.run(review_input=sample_review_input)

        # Should stop at round 3 (convergence: 0 new findings)
        assert call_count == 3
        assert report.rounds_completed == 3

    def test_convergence_stops_early(
        self,
        sample_review_input: ReviewInput,
        mock_settings: MagicMock,
        mock_llm_client: MagicMock,
    ) -> None:
        """Loop stops when round produces 0 new findings."""
        mock_settings.max_deepening_rounds = 5
        orchestrator = Orchestrator(
            settings=mock_settings,
            llm_client=mock_llm_client,
        )

        round1 = [_make_agent_result("security")]
        # Round 2: same finding as round 1 (duplicate)
        round2 = [_make_agent_result("security")]

        call_count = 0

        def side_effect(**kwargs: object) -> list[AgentResult]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return round1
            return round2

        with patch.object(orchestrator, "_run_agents", side_effect=side_effect):
            report = orchestrator.run(review_input=sample_review_input)

        # Should stop at round 2 (convergence: duplicate findings)
        assert call_count == 2
        assert report.rounds_completed == 2

    def test_previous_findings_passed_to_agents(
        self,
        sample_review_input: ReviewInput,
        mock_settings: MagicMock,
        mock_llm_client: MagicMock,
    ) -> None:
        """Round 2+ passes previous findings to _run_agents."""
        mock_settings.max_deepening_rounds = 2
        orchestrator = Orchestrator(
            settings=mock_settings,
            llm_client=mock_llm_client,
        )

        round1 = [_make_agent_result("security")]
        round2 = [
            AgentResult(
                agent_name="security",
                findings=[],
                summary="No new issues.",
                execution_time_seconds=0.5,
            ),
        ]

        previous_findings_received: list[object] = []

        def side_effect(
            **kwargs: object,
        ) -> list[AgentResult]:
            previous_findings_received.append(
                kwargs.get("previous_findings"),
            )
            if len(previous_findings_received) == 1:
                return round1
            return round2

        with patch.object(orchestrator, "_run_agents", side_effect=side_effect):
            orchestrator.run(review_input=sample_review_input)

        # Round 1: no previous findings
        assert previous_findings_received[0] is None
        # Round 2: has previous findings from round 1
        assert previous_findings_received[1] is not None
        assert len(previous_findings_received[1]) > 0  # type: ignore[arg-type]

    def test_findings_accumulated_across_rounds(
        self,
        sample_review_input: ReviewInput,
        mock_settings: MagicMock,
        mock_llm_client: MagicMock,
        mock_synthesis_response: SynthesisResponse,
    ) -> None:
        """Final report contains findings from all rounds."""
        mock_settings.max_deepening_rounds = 2
        mock_llm_client.complete.return_value = mock_synthesis_response
        orchestrator = Orchestrator(
            settings=mock_settings,
            llm_client=mock_llm_client,
        )

        round1 = [_make_agent_result("security")]
        round2_finding = Finding(
            file_path="src/deep.py",
            line_number=50,
            severity="critical",
            category="security",
            title="Deep finding",
            description="Found on second pass.",
            suggestion="Fix.",
        )
        round2 = [
            AgentResult(
                agent_name="security",
                findings=[round2_finding],
                summary="Deeper issue.",
                execution_time_seconds=1.0,
            ),
        ]

        call_count = 0

        def side_effect(**kwargs: object) -> list[AgentResult]:
            nonlocal call_count
            call_count += 1
            return round1 if call_count == 1 else round2

        with patch.object(orchestrator, "_run_agents", side_effect=side_effect):
            report = orchestrator.run(review_input=sample_review_input)

        # Both rounds' findings should be in the report
        all_finding_titles = [f.title for r in report.agent_results for f in r.findings]
        assert "security finding" in all_finding_titles
        assert "Deep finding" in all_finding_titles
        assert report.rounds_completed == 2
