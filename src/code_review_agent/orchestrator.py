from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from code_review_agent.agents import (
    PerformanceAgent,
    SecurityAgent,
    StyleAgent,
    TestCoverageAgent,
)
from code_review_agent.models import (
    AgentResult,
    DiffFile,
    ReviewInput,
    ReviewReport,
    SynthesisResponse,
)
from code_review_agent.token_budget import (
    CharBasedEstimator,
    TokenEstimator,
    resolve_prompt_budget,
)

if TYPE_CHECKING:
    from code_review_agent.agents.base import BaseAgent
    from code_review_agent.config import Settings
    from code_review_agent.llm_client import LLMClient

logger = structlog.get_logger(__name__)

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
    ) -> None:
        self._settings = settings
        self._llm_client = llm_client
        self._estimator = token_estimator or CharBasedEstimator()
        self._budget = resolve_prompt_budget(settings)

    def run(self, review_input: ReviewInput) -> ReviewReport:
        """Execute all agents concurrently, synthesize results, and return a report."""
        review_input = self._apply_token_budget(review_input)

        agents: list[BaseAgent] = [
            SecurityAgent(llm_client=self._llm_client),
            PerformanceAgent(llm_client=self._llm_client),
            StyleAgent(llm_client=self._llm_client),
            TestCoverageAgent(llm_client=self._llm_client),
        ]

        agent_results = self._run_agents(
            agents=agents,
            review_input=review_input,
        )

        synthesis = self._synthesize(agent_results=agent_results)

        return ReviewReport(
            pr_url=review_input.pr_url,
            reviewed_at=datetime.now(tz=UTC),
            agent_results=agent_results,
            overall_summary=synthesis.overall_summary,
            risk_level=synthesis.risk_level,
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
        )

    def _run_agents(
        self,
        *,
        agents: list[BaseAgent],
        review_input: ReviewInput,
    ) -> list[AgentResult]:
        """Run all agents concurrently and collect their results.

        LLM-level errors (parse failures, empty responses) are handled inside
        each agent and returned as ``AgentResult`` with ``status="failed"``.
        Only infrastructure errors (network, auth) are caught here.
        """
        results: list[AgentResult] = []
        max_workers = min(self._settings.max_concurrent_agents, len(agents))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_agent = {
                executor.submit(agent.review, review_input): agent for agent in agents
            }

            for future in as_completed(future_to_agent):
                agent = future_to_agent[future]
                try:
                    results.append(future.result())
                except Exception:
                    logger.exception(
                        "agent crashed, continuing with partial results",
                        agent=agent.name,
                    )

        return results

    def _synthesize(self, *, agent_results: list[AgentResult]) -> SynthesisResponse:
        """Use the LLM to produce an overall summary and risk level."""
        findings_summary_parts: list[str] = []
        for result in agent_results:
            findings_summary_parts.append(
                f"Agent: {result.agent_name}\n"
                f"Summary: {result.summary}\n"
                f"Finding count: {len(result.findings)}\n"
                f"Findings: {json.dumps([f.model_dump() for f in result.findings], indent=2)}"
            )

        user_prompt = "Here are the results from all review agents:\n\n" + "\n\n---\n\n".join(
            findings_summary_parts
        )

        return self._llm_client.complete(
            system_prompt=_SYNTHESIS_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_model=SynthesisResponse,
        )
