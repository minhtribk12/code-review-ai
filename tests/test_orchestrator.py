from __future__ import annotations

from unittest.mock import MagicMock, call, patch

from code_review_agent.models import (
    AgentResult,
    Finding,
    ReviewEvent,
    ReviewInput,
    ReviewReport,
    SynthesisResponse,
    ValidatedFinding,
    ValidationResponse,
    ValidationVerdict,
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


def _make_validation_response(
    findings: list[Finding],
    verdicts: list[ValidationVerdict],
) -> ValidationResponse:
    """Build a ValidationResponse with given verdicts for each finding."""
    validated = [
        ValidatedFinding(
            original_finding=f,
            verdict=v,
            reasoning=f"Verdict for {f.title}",
        )
        for f, v in zip(findings, verdicts, strict=True)
    ]
    fp_count = sum(1 for v in verdicts if v == ValidationVerdict.LIKELY_FALSE_POSITIVE)
    return ValidationResponse(
        validated_findings=validated,
        false_positive_count=fp_count,
        validation_summary=f"{fp_count} false positives removed.",
    )


class TestValidationLoop:
    """Test the validation loop (CRA-49)."""

    def test_validation_disabled_by_default(
        self,
        sample_review_input: ReviewInput,
        mock_settings: MagicMock,
        mock_llm_client: MagicMock,
        mock_synthesis_response: SynthesisResponse,
    ) -> None:
        """Validation is off by default -- no validation_result in report."""
        assert mock_settings.is_validation_enabled is False
        mock_llm_client.complete.return_value = mock_synthesis_response
        orchestrator = Orchestrator(settings=mock_settings, llm_client=mock_llm_client)

        results = [_make_agent_result("security"), _make_agent_result("performance")]

        with patch.object(orchestrator, "_run_agents", return_value=results):
            report = orchestrator.run(review_input=sample_review_input)

        assert report.validation_result is None

    def test_validation_filters_false_positives(
        self,
        sample_review_input: ReviewInput,
        mock_settings: MagicMock,
        mock_llm_client: MagicMock,
        mock_synthesis_response: SynthesisResponse,
    ) -> None:
        """False positives are removed from agent results."""
        mock_settings.is_validation_enabled = True
        mock_settings.max_validation_rounds = 1

        results = [_make_agent_result("security"), _make_agent_result("performance")]
        all_findings = [f for r in results for f in r.findings]

        validation_resp = _make_validation_response(
            all_findings,
            [ValidationVerdict.LIKELY_FALSE_POSITIVE, ValidationVerdict.CONFIRMED],
        )

        # First call = synthesis, second call = validation
        mock_llm_client.complete.side_effect = [mock_synthesis_response, validation_resp]
        orchestrator = Orchestrator(settings=mock_settings, llm_client=mock_llm_client)

        with patch.object(orchestrator, "_run_agents", return_value=results):
            report = orchestrator.run(review_input=sample_review_input)

        # One false positive removed
        remaining_titles = [f.title for r in report.agent_results for f in r.findings]
        assert "security finding" not in remaining_titles
        assert "performance finding" in remaining_titles
        assert report.validation_result is not None
        assert report.validation_result.false_positive_count == 1

    def test_validation_preserves_confirmed_and_uncertain(
        self,
        sample_review_input: ReviewInput,
        mock_settings: MagicMock,
        mock_llm_client: MagicMock,
        mock_synthesis_response: SynthesisResponse,
    ) -> None:
        """Confirmed and uncertain findings are kept in the report."""
        mock_settings.is_validation_enabled = True
        mock_settings.max_validation_rounds = 1

        results = [_make_agent_result("security"), _make_agent_result("performance")]
        all_findings = [f for r in results for f in r.findings]

        validation_resp = _make_validation_response(
            all_findings,
            [ValidationVerdict.CONFIRMED, ValidationVerdict.UNCERTAIN],
        )

        mock_llm_client.complete.side_effect = [mock_synthesis_response, validation_resp]
        orchestrator = Orchestrator(settings=mock_settings, llm_client=mock_llm_client)

        with patch.object(orchestrator, "_run_agents", return_value=results):
            report = orchestrator.run(review_input=sample_review_input)

        remaining_titles = [f.title for r in report.agent_results for f in r.findings]
        assert "security finding" in remaining_titles
        assert "performance finding" in remaining_titles

    def test_adjusted_severity_applied(
        self,
        sample_review_input: ReviewInput,
        mock_settings: MagicMock,
        mock_llm_client: MagicMock,
        mock_synthesis_response: SynthesisResponse,
    ) -> None:
        """Validator can downgrade severity of a finding."""
        mock_settings.is_validation_enabled = True
        mock_settings.max_validation_rounds = 1

        results = [_make_agent_result("security"), _make_agent_result("performance")]
        sec_finding = results[0].findings[0]
        perf_finding = results[1].findings[0]

        validation_resp = ValidationResponse(
            validated_findings=[
                ValidatedFinding(
                    original_finding=sec_finding,
                    verdict=ValidationVerdict.CONFIRMED,
                    reasoning="Real issue but in test code, lower severity.",
                    adjusted_severity="low",
                ),
                ValidatedFinding(
                    original_finding=perf_finding,
                    verdict=ValidationVerdict.CONFIRMED,
                    reasoning="Valid.",
                ),
            ],
            false_positive_count=0,
            validation_summary="0 false positives removed.",
        )

        mock_llm_client.complete.side_effect = [mock_synthesis_response, validation_resp]
        orchestrator = Orchestrator(settings=mock_settings, llm_client=mock_llm_client)

        with patch.object(orchestrator, "_run_agents", return_value=results):
            report = orchestrator.run(review_input=sample_review_input)

        adjusted = report.agent_results[0].findings[0]
        assert adjusted.severity == "low"

    def test_risk_level_recalculated_after_filtering(
        self,
        sample_review_input: ReviewInput,
        mock_settings: MagicMock,
        mock_llm_client: MagicMock,
    ) -> None:
        """Risk level adjusts downward when critical findings are removed."""
        mock_settings.is_validation_enabled = True
        mock_settings.max_validation_rounds = 1

        critical_finding = Finding(
            file_path="src/app.py",
            line_number=1,
            severity="critical",
            category="security",
            title="Critical vuln",
            description="A critical vulnerability.",
        )
        low_finding = Finding(
            file_path="src/app.py",
            line_number=10,
            severity="low",
            category="style",
            title="Style issue",
            description="A minor style issue.",
        )
        results = [
            AgentResult(
                agent_name="security",
                findings=[critical_finding],
                summary="Critical issue.",
                execution_time_seconds=1.0,
            ),
            AgentResult(
                agent_name="style",
                findings=[low_finding],
                summary="Style issue.",
                execution_time_seconds=1.0,
            ),
        ]

        synthesis = SynthesisResponse(
            overall_summary="Critical security issue found.",
            risk_level="critical",
        )
        validation_resp = _make_validation_response(
            [critical_finding, low_finding],
            [ValidationVerdict.LIKELY_FALSE_POSITIVE, ValidationVerdict.CONFIRMED],
        )

        mock_llm_client.complete.side_effect = [synthesis, validation_resp]
        orchestrator = Orchestrator(settings=mock_settings, llm_client=mock_llm_client)

        with patch.object(orchestrator, "_run_agents", return_value=results):
            report = orchestrator.run(review_input=sample_review_input)

        # Critical removed, only low remains -- risk should be low or medium
        assert report.risk_level in ("low", "medium")

    def test_validation_failure_degrades_gracefully(
        self,
        sample_review_input: ReviewInput,
        mock_settings: MagicMock,
        mock_llm_client: MagicMock,
        mock_synthesis_response: SynthesisResponse,
    ) -> None:
        """If validation LLM call fails, return unfiltered results."""
        mock_settings.is_validation_enabled = True
        mock_settings.max_validation_rounds = 1

        results = [_make_agent_result("security"), _make_agent_result("performance")]

        # Synthesis succeeds, validation raises
        mock_llm_client.complete.side_effect = [
            mock_synthesis_response,
            RuntimeError("LLM unavailable"),
        ]
        orchestrator = Orchestrator(settings=mock_settings, llm_client=mock_llm_client)

        with patch.object(orchestrator, "_run_agents", return_value=results):
            report = orchestrator.run(review_input=sample_review_input)

        # All findings preserved despite validation failure
        assert len(report.agent_results) == 2
        total = sum(len(r.findings) for r in report.agent_results)
        assert total == 2
        assert report.validation_result is None

    def test_validation_events_emitted(
        self,
        sample_review_input: ReviewInput,
        mock_settings: MagicMock,
        mock_llm_client: MagicMock,
        mock_synthesis_response: SynthesisResponse,
    ) -> None:
        """VALIDATION_STARTED and VALIDATION_COMPLETED events fire."""
        mock_settings.is_validation_enabled = True
        mock_settings.max_validation_rounds = 1

        results = [_make_agent_result("security"), _make_agent_result("performance")]
        all_findings = [f for r in results for f in r.findings]

        validation_resp = _make_validation_response(
            all_findings,
            [ValidationVerdict.CONFIRMED, ValidationVerdict.CONFIRMED],
        )

        mock_llm_client.complete.side_effect = [mock_synthesis_response, validation_resp]
        event_callback = MagicMock()
        orchestrator = Orchestrator(
            settings=mock_settings,
            llm_client=mock_llm_client,
            on_event=event_callback,
        )

        with patch.object(orchestrator, "_run_agents", return_value=results):
            orchestrator.run(review_input=sample_review_input)

        validation_calls = [
            c
            for c in event_callback.call_args_list
            if c[0][0] in (ReviewEvent.VALIDATION_STARTED, ReviewEvent.VALIDATION_COMPLETED)
        ]
        assert len(validation_calls) == 2
        assert validation_calls[0] == call(
            ReviewEvent.VALIDATION_STARTED,
            "validation",
            None,
        )
        assert validation_calls[1] == call(
            ReviewEvent.VALIDATION_COMPLETED,
            "validation",
            None,
        )

    def test_multi_round_validation_rechecks_uncertain(
        self,
        sample_review_input: ReviewInput,
        mock_settings: MagicMock,
        mock_llm_client: MagicMock,
        mock_synthesis_response: SynthesisResponse,
    ) -> None:
        """Uncertain findings are re-checked in subsequent validation rounds."""
        mock_settings.is_validation_enabled = True
        mock_settings.max_validation_rounds = 2

        results = [_make_agent_result("security"), _make_agent_result("performance")]
        all_findings = [f for r in results for f in r.findings]

        # Round 1: security=uncertain, performance=confirmed
        round1_resp = _make_validation_response(
            all_findings,
            [ValidationVerdict.UNCERTAIN, ValidationVerdict.CONFIRMED],
        )
        # Round 2: uncertain finding now confirmed
        round2_resp = _make_validation_response(
            [all_findings[0]],
            [ValidationVerdict.CONFIRMED],
        )

        mock_llm_client.complete.side_effect = [
            mock_synthesis_response,
            round1_resp,
            round2_resp,
        ]
        orchestrator = Orchestrator(settings=mock_settings, llm_client=mock_llm_client)

        with patch.object(orchestrator, "_run_agents", return_value=results):
            report = orchestrator.run(review_input=sample_review_input)

        # Both findings should survive (both confirmed after 2 rounds)
        remaining_titles = [f.title for r in report.agent_results for f in r.findings]
        assert "security finding" in remaining_titles
        assert "performance finding" in remaining_titles
        # 3 LLM calls: synthesis + 2 validation rounds
        assert mock_llm_client.complete.call_count == 3
