from __future__ import annotations

import time
from abc import ABC
from typing import TYPE_CHECKING

import structlog

from code_review_agent.models import AgentResult, FindingsResponse, ReviewInput

if TYPE_CHECKING:
    from code_review_agent.llm_client import LLMClient

logger = structlog.get_logger(__name__)


class BaseAgent(ABC):
    """Abstract base class for all review agents.

    Subclasses must set ``name`` and ``system_prompt`` class attributes. The
    ``review`` method formats the diff into a user prompt, calls the LLM, and
    wraps the result in an ``AgentResult`` with timing information.
    """

    name: str
    system_prompt: str

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm_client = llm_client

    def review(self, review_input: ReviewInput) -> AgentResult:
        """Run the agent review on the provided input and return findings."""
        user_prompt = self._format_user_prompt(review_input=review_input)

        logger.info("agent review started", agent=self.name)
        start = time.monotonic()

        response = self._llm_client.complete(
            system_prompt=self.system_prompt,
            user_prompt=user_prompt,
            response_model=FindingsResponse,
        )

        elapsed = time.monotonic() - start
        logger.info(
            "agent review completed",
            agent=self.name,
            finding_count=len(response.findings),
            elapsed_seconds=round(elapsed, 2),
        )

        return AgentResult(
            agent_name=self.name,
            findings=response.findings,
            summary=response.summary,
            execution_time_seconds=round(elapsed, 2),
        )

    def _format_user_prompt(self, *, review_input: ReviewInput) -> str:
        """Build the user prompt from the review input."""
        parts: list[str] = []

        if review_input.pr_title is not None:
            parts.append(f"PR Title: {review_input.pr_title}")
        if review_input.pr_description:
            parts.append(f"PR Description: {review_input.pr_description}")

        parts.append("\n--- DIFF START ---")
        for diff_file in review_input.diff_files:
            parts.append(f"\nFile: {diff_file.filename} (status: {diff_file.status})")
            parts.append(diff_file.patch)
        parts.append("--- DIFF END ---")

        return "\n".join(parts)
