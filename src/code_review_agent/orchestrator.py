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
    ReviewInput,
    ReviewReport,
    SynthesisResponse,
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

    def __init__(self, settings: Settings, llm_client: LLMClient) -> None:
        self._settings = settings
        self._llm_client = llm_client

    def run(self, review_input: ReviewInput) -> ReviewReport:
        """Execute all agents concurrently, synthesize results, and return a report."""
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
