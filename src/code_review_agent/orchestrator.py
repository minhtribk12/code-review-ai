from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from code_review_agent.agents import AGENT_REGISTRY, ALL_AGENT_NAMES
from code_review_agent.dedup import deduplicate_agent_results
from code_review_agent.models import (
    AgentResult,
    AgentStatus,
    Confidence,
    DiffFile,
    Finding,
    ReviewEvent,
    ReviewInput,
    ReviewReport,
    Severity,
    SynthesisResponse,
    TokenUsage,
)
from code_review_agent.prompt_security import detect_suspicious_patterns
from code_review_agent.token_budget import (
    CharBasedEstimator,
    TokenEstimator,
    default_agents_for_tier,
    estimate_cost,
    resolve_prompt_budget,
)

if TYPE_CHECKING:
    from code_review_agent.agents.base import BaseAgent
    from code_review_agent.config import Settings
    from code_review_agent.llm_client import LLMClient
    from code_review_agent.progress import EventCallback

logger = structlog.get_logger(__name__)

# Severity ordering: index 0 = lowest. Used for risk level validation.
_SEVERITY_ORDER: list[Severity] = [Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]

_SYNTHESIS_SYSTEM_PROMPT = (
    "You are a senior engineering lead synthesizing code review results from "
    "multiple specialized review agents. You have received findings from security, "
    "performance, style, and test coverage agents.\n\n"
    "Your task:\n"
    "1. Write a concise overall_summary (2-4 sentences) that highlights the most "
    "important findings and the general quality of the changes.\n"
    "2. Assign a risk_level (low, medium, high, or critical) based on the severity "
    "and quantity of findings across all agents.\n\n"
    "Guidelines for risk_level:\n"
    "- critical: any critical security finding, or multiple high-severity issues\n"
    "- high: high-severity findings present, or many medium-severity issues\n"
    "- medium: only medium and low severity findings\n"
    "- low: few or no findings, all low severity"
)


class Orchestrator:
    """Coordinates multiple review agents and synthesizes their results."""

    def __init__(
        self,
        settings: Settings,
        llm_client: LLMClient,
        token_estimator: TokenEstimator | None = None,
        on_event: EventCallback | None = None,
    ) -> None:
        self._settings = settings
        self._llm_client = llm_client
        self._estimator = token_estimator or CharBasedEstimator()
        self._budget = resolve_prompt_budget(settings)
        self._on_event = on_event

    def run(
        self,
        review_input: ReviewInput,
        *,
        agent_names: list[str] | None = None,
    ) -> ReviewReport:
        """Execute agents with optional iterative deepening, then synthesize.

        When ``max_deepening_rounds > 1``, agents run multiple rounds.
        Each round passes the previous round's findings to agents via
        ``previous_findings``. The loop stops early if a round produces
        zero new findings (convergence) or when the max is reached.
        """
        review_input = self._apply_token_budget(review_input)
        injection_findings = self._scan_for_injection(review_input)

        selected_names = agent_names or default_agents_for_tier(self._settings.token_tier)
        agents: list[BaseAgent] = self._build_agents(selected_names)

        logger.info(
            "running agents",
            selected=[a.name for a in agents],
            total_registered=len(ALL_AGENT_NAMES),
        )

        max_rounds = self._settings.max_deepening_rounds
        all_findings: list[Finding] = []
        all_agent_results: list[AgentResult] = []
        rounds_completed = 0

        for round_num in range(1, max_rounds + 1):
            previous = all_findings if round_num > 1 else None

            round_results = self._run_agents(
                agents=agents,
                review_input=review_input,
                previous_findings=previous,
            )

            # Deduplicate this round's findings against all previous
            round_results = deduplicate_agent_results(
                round_results,
                strategy=self._settings.dedup_strategy,
            )

            # Count genuinely new findings
            existing_keys = {(f.file_path, f.line_number, f.title) for f in all_findings}
            new_findings: list[Finding] = []
            for result in round_results:
                for finding in result.findings:
                    key = (finding.file_path, finding.line_number, finding.title)
                    if key not in existing_keys:
                        new_findings.append(finding)
                        existing_keys.add(key)

            rounds_completed = round_num

            # Merge round results into cumulative results
            if round_num == 1:
                all_agent_results = round_results
            else:
                all_agent_results = self._merge_round_results(
                    all_agent_results,
                    round_results,
                )

            all_findings.extend(new_findings)

            logger.info(
                "deepening round complete",
                round=round_num,
                new_findings=len(new_findings),
                total_findings=len(all_findings),
            )

            if self._is_token_budget_exceeded():
                logger.warning(
                    "token budget exceeded, stopping deepening",
                    round=round_num,
                )
                break

            # Convergence: stop if no new findings this round
            if round_num > 1 and len(new_findings) == 0:
                logger.info(
                    "deepening converged, no new findings",
                    round=round_num,
                )
                break

        agent_results = all_agent_results

        if injection_findings:
            agent_results = self._inject_security_findings(
                agent_results,
                injection_findings,
            )

        successful_results = [r for r in agent_results if r.status != AgentStatus.FAILED]

        if len(successful_results) <= 1:
            report = self._build_report_without_synthesis(
                review_input=review_input,
                agent_results=agent_results,
                successful_results=successful_results,
            )
            return report.model_copy(
                update={"rounds_completed": rounds_completed},
            )

        self._emit(ReviewEvent.SYNTHESIS_STARTED, "synthesis")
        synthesis = self._synthesize(agent_results=agent_results)
        self._emit(ReviewEvent.SYNTHESIS_COMPLETED, "synthesis")
        validated_risk = self._validate_risk_level(
            synthesis.risk_level,
            agent_results,
        )

        return ReviewReport(
            pr_url=review_input.pr_url,
            reviewed_at=datetime.now(tz=UTC),
            agent_results=agent_results,
            overall_summary=synthesis.overall_summary,
            risk_level=validated_risk,
            fetch_warnings=review_input.fetch_warnings,
            token_usage=self._build_token_usage(),
            rounds_completed=rounds_completed,
        )

    def _emit(self, event: ReviewEvent, agent_name: str, elapsed: float | None = None) -> None:
        """Fire an event to the registered callback, if any."""
        if self._on_event is not None:
            self._on_event(event, agent_name, elapsed)

    def _build_token_usage(self) -> TokenUsage:
        """Build a TokenUsage with cost estimate from the LLM client's cumulative counters."""
        usage = self._llm_client.get_usage()
        cost = estimate_cost(
            model=self._settings.llm_model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            input_price_per_m=self._settings.llm_input_price_per_m,
            output_price_per_m=self._settings.llm_output_price_per_m,
        )
        return TokenUsage(
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            llm_calls=usage.llm_calls,
            estimated_cost_usd=cost,
        )

    def _is_token_budget_exceeded(self) -> bool:
        """Check if cumulative token usage exceeds the per-review budget."""
        max_tokens = self._settings.max_tokens_per_review
        if max_tokens is None:
            return False
        usage = self._llm_client.get_usage()
        return usage.total_tokens >= max_tokens

    def _scan_for_injection(self, review_input: ReviewInput) -> list[Finding]:
        """Scan diff content for suspicious prompt injection patterns."""
        all_patches = "\n".join(f.patch for f in review_input.diff_files)
        if not all_patches:
            return []

        patterns = detect_suspicious_patterns(all_patches)
        findings: list[Finding] = []

        for pattern in patterns:
            if not pattern.is_high_confidence:
                continue
            # Severity LOW intentionally: injection detection is heuristic,
            # false positives are common. LOW avoids inflating risk level.
            findings.append(
                Finding(
                    severity=Severity.LOW,
                    category="Prompt Security",
                    title=f"Potential prompt injection: {pattern.name}",
                    description=(
                        f"The diff contains text matching a known injection pattern: "
                        f"'{pattern.matched_text}'. This may be legitimate code, but "
                        f"could also be an attempt to manipulate the review."
                    ),
                    confidence=Confidence.LOW,
                )
            )

        return findings

    def _inject_security_findings(
        self,
        agent_results: list[AgentResult],
        injection_findings: list[Finding],
    ) -> list[AgentResult]:
        """Return a new list with injection findings added to the first successful result."""
        updated: list[AgentResult] = []
        is_injected = False

        for result in agent_results:
            if not is_injected and result.status != AgentStatus.FAILED:
                merged_findings = list(result.findings) + injection_findings
                updated.append(
                    AgentResult(
                        agent_name=result.agent_name,
                        findings=merged_findings,
                        summary=result.summary,
                        execution_time_seconds=result.execution_time_seconds,
                        status=result.status,
                        error_message=result.error_message,
                    )
                )
                is_injected = True
            else:
                updated.append(result)

        return updated

    def _build_agents(self, agent_names: list[str]) -> list[BaseAgent]:
        """Instantiate agents by name from the registry."""
        agents: list[BaseAgent] = []
        for name in agent_names:
            agent_cls = AGENT_REGISTRY.get(name)
            if agent_cls is None:
                logger.warning(
                    "unknown agent name, skipping",
                    agent=name,
                    available=ALL_AGENT_NAMES,
                )
                continue
            agents.append(agent_cls(llm_client=self._llm_client))
        return agents

    def _build_report_without_synthesis(
        self,
        *,
        review_input: ReviewInput,
        agent_results: list[AgentResult],
        successful_results: list[AgentResult],
    ) -> ReviewReport:
        """Build report without an LLM synthesis call.

        Used when 0 or 1 agents succeeded -- synthesis adds no value
        and this saves one LLM call.
        """
        if successful_results:
            result = successful_results[0]
            overall_summary = result.summary
            max_severity = max(
                (f.severity for f in result.findings),
                key=lambda s: _SEVERITY_ORDER.index(s),
                default=Severity.LOW,
            )
            logger.info(
                "skipping synthesis, single agent result",
                agent=result.agent_name,
                risk_level=max_severity,
            )
        else:
            overall_summary = "No agents completed successfully."
            max_severity = Severity.LOW
            logger.warning("skipping synthesis, no successful agent results")

        return ReviewReport(
            pr_url=review_input.pr_url,
            reviewed_at=datetime.now(tz=UTC),
            agent_results=agent_results,
            overall_summary=overall_summary,
            risk_level=max_severity,
            fetch_warnings=review_input.fetch_warnings,
            token_usage=self._build_token_usage(),
        )

    def _apply_token_budget(self, review_input: ReviewInput) -> ReviewInput:
        """Estimate token usage and truncate if over budget.

        Two-pass strategy: sort files by change volume, keep full diff for
        most-changed files that fit the budget, replace the rest with a
        one-line summary.
        """
        all_patches = "".join(f.patch for f in review_input.diff_files)
        estimated_tokens = self._estimator.estimate(all_patches)

        if estimated_tokens <= self._budget:
            logger.debug(
                "diff within token budget",
                estimated_tokens=estimated_tokens,
                budget=self._budget,
            )
            return review_input

        logger.warning(
            "diff exceeds token budget, truncating",
            estimated_tokens=estimated_tokens,
            budget=self._budget,
            file_count=len(review_input.diff_files),
        )

        return self._truncate_review_input(review_input)

    def _truncate_review_input(self, review_input: ReviewInput) -> ReviewInput:
        """Two-pass truncation: full diff for top files, summary for rest."""
        # Sort by change volume (most changed first)
        sorted_files = sorted(
            review_input.diff_files,
            key=lambda f: f.patch.count("\n"),
            reverse=True,
        )

        included_full: list[DiffFile] = []
        included_summary: list[DiffFile] = []
        remaining_budget = self._budget

        for diff_file in sorted_files:
            file_tokens = self._estimator.estimate(diff_file.patch)

            if remaining_budget >= file_tokens:
                included_full.append(diff_file)
                remaining_budget -= file_tokens
            else:
                added = sum(1 for line in diff_file.patch.splitlines() if line.startswith("+"))
                removed = sum(1 for line in diff_file.patch.splitlines() if line.startswith("-"))
                summary_patch = f"[TRUNCATED] +{added}/-{removed} lines"
                included_summary.append(
                    DiffFile(
                        filename=diff_file.filename,
                        patch=summary_patch,
                        status=diff_file.status,
                    )
                )

        logger.info(
            "truncation complete",
            full_files=len(included_full),
            truncated_files=len(included_summary),
        )

        return ReviewInput(
            diff_files=included_full + included_summary,
            pr_url=review_input.pr_url,
            pr_title=review_input.pr_title,
            pr_description=review_input.pr_description,
            fetch_warnings=review_input.fetch_warnings,
        )

    def _merge_round_results(
        self,
        cumulative: list[AgentResult],
        new_round: list[AgentResult],
    ) -> list[AgentResult]:
        """Merge a new round's results into the cumulative results.

        For each agent, combine findings from all rounds into a single
        AgentResult. Execution times are summed.
        """
        by_name: dict[str, AgentResult] = {r.agent_name: r for r in cumulative}

        for result in new_round:
            existing = by_name.get(result.agent_name)
            if existing is None:
                by_name[result.agent_name] = result
            else:
                merged_findings = list(existing.findings) + list(result.findings)
                by_name[result.agent_name] = AgentResult(
                    agent_name=result.agent_name,
                    findings=merged_findings,
                    summary=result.summary,
                    execution_time_seconds=(
                        existing.execution_time_seconds + result.execution_time_seconds
                    ),
                    status=result.status,
                    error_message=result.error_message,
                )

        return list(by_name.values())

    def _run_single_agent(
        self,
        agent: BaseAgent,
        review_input: ReviewInput,
        previous_findings: list[Finding] | None = None,
    ) -> AgentResult:
        """Run a single agent, emitting start/complete/fail events."""
        self._emit(ReviewEvent.AGENT_STARTED, agent.name)
        result = agent.review(review_input, previous_findings=previous_findings)
        if result.status == AgentStatus.FAILED:
            self._emit(
                ReviewEvent.AGENT_FAILED,
                agent.name,
                result.execution_time_seconds,
            )
        else:
            self._emit(
                ReviewEvent.AGENT_COMPLETED,
                agent.name,
                result.execution_time_seconds,
            )
        return result

    def _run_agents(
        self,
        *,
        agents: list[BaseAgent],
        review_input: ReviewInput,
        previous_findings: list[Finding] | None = None,
    ) -> list[AgentResult]:
        """Run all agents concurrently with a total time limit.

        LLM-level errors (parse failures, empty responses) are handled inside
        each agent and returned as ``AgentResult`` with ``status="failed"``.
        Infrastructure errors (network, auth) are caught here.
        Agents that don't complete within ``max_review_seconds`` are marked
        as failed with a timeout error.
        """
        results: list[AgentResult] = []
        max_workers = min(self._settings.max_concurrent_agents, len(agents))
        timeout = self._settings.max_review_seconds

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_agent = {
                executor.submit(
                    self._run_single_agent,
                    agent,
                    review_input,
                    previous_findings,
                ): agent
                for agent in agents
            }

            done, not_done = wait(future_to_agent, timeout=timeout)

            # Collect completed results
            for future in done:
                agent = future_to_agent[future]
                try:
                    results.append(future.result())
                except Exception:
                    logger.exception(
                        "agent crashed, continuing with partial results",
                        agent=agent.name,
                    )
                    self._emit(ReviewEvent.AGENT_FAILED, agent.name)

            # Mark timed-out agents as failed
            for future in not_done:
                agent = future_to_agent[future]
                future.cancel()
                logger.warning(
                    "agent timed out",
                    agent=agent.name,
                    timeout_seconds=timeout,
                )
                self._emit(ReviewEvent.AGENT_FAILED, agent.name, float(timeout))
                results.append(
                    AgentResult(
                        agent_name=agent.name,
                        findings=[],
                        summary="",
                        execution_time_seconds=float(timeout),
                        status=AgentStatus.FAILED,
                        error_message=f"Review timed out after {timeout}s",
                    )
                )

        return results

    def _synthesize(self, *, agent_results: list[AgentResult]) -> SynthesisResponse:
        """Use the LLM to produce an overall summary and risk level."""
        all_findings = [f for r in agent_results for f in r.findings]

        # Compute stats to ground the LLM and prevent hallucination
        severity_counts = {s.value: 0 for s in Severity}
        for finding in all_findings:
            severity_counts[finding.severity.value] += 1

        max_severity = max(
            (f.severity for f in all_findings),
            key=lambda s: _SEVERITY_ORDER.index(s),
            default=Severity.LOW,
        )

        findings_summary_parts: list[str] = []
        for result in agent_results:
            findings_summary_parts.append(
                f"Agent: {result.agent_name}\n"
                f"Summary: {result.summary}\n"
                f"Finding count: {len(result.findings)}\n"
                f"Findings: {json.dumps([f.model_dump() for f in result.findings], indent=2)}"
            )

        stats_block = (
            f"\n\nFinding statistics:\n"
            f"- Total findings: {len(all_findings)}\n"
            f"- By severity: {severity_counts}\n"
            f"- Maximum severity: {max_severity.value}\n"
            f"- Your risk_level MUST NOT exceed '{max_severity.value}' "
            f"unless multiple findings justify a one-level escalation."
        )

        user_prompt = (
            "Here are the results from all review agents:\n\n"
            + "\n\n---\n\n".join(findings_summary_parts)
            + stats_block
        )

        return self._llm_client.complete(
            system_prompt=_SYNTHESIS_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_model=SynthesisResponse,
        )

    def _validate_risk_level(
        self,
        proposed_risk: Severity,
        agent_results: list[AgentResult],
    ) -> Severity:
        """Validate the LLM-proposed risk level against actual findings.

        Rules:
        1. Zero findings -> LOW (always).
        2. Proposed risk may exceed max finding severity by at most 1 level.
        3. If proposed risk exceeds the allowed level, override and log.
        """
        all_findings = [f for r in agent_results for f in r.findings]

        # Rule 1: no findings = LOW
        if not all_findings:
            if proposed_risk != Severity.LOW:
                logger.warning(
                    "overriding risk level, no findings",
                    proposed=proposed_risk.value,
                    validated=Severity.LOW.value,
                )
            return Severity.LOW

        max_severity = max(
            (f.severity for f in all_findings),
            key=lambda s: _SEVERITY_ORDER.index(s),
        )

        max_index = _SEVERITY_ORDER.index(max_severity)
        proposed_index = _SEVERITY_ORDER.index(proposed_risk)

        # Rule 2: allow at most 1 level escalation
        allowed_index = min(max_index + 1, len(_SEVERITY_ORDER) - 1)

        if proposed_index <= allowed_index:
            return proposed_risk

        # Rule 3: override
        allowed_risk = _SEVERITY_ORDER[allowed_index]
        logger.warning(
            "overriding hallucinated risk level",
            proposed=proposed_risk.value,
            max_finding_severity=max_severity.value,
            validated=allowed_risk.value,
        )
        return allowed_risk
