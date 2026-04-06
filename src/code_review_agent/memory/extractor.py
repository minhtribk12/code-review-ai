"""LLM-based fact extraction from review results.

Analyzes completed reviews and triage decisions to extract reusable
facts about the project, team preferences, and code conventions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from code_review_agent.llm_client import LLMClient
    from code_review_agent.memory.fact_store import FactStore
    from code_review_agent.models import ReviewReport

logger = structlog.get_logger(__name__)

_SYSTEM_PROMPT = """\
You are a memory extraction system. Analyze the code review results below
and extract discrete, reusable facts about this project.

Extract facts in these categories:
- project_convention: coding patterns, naming conventions, architecture decisions
- code_style: formatting preferences, idiom choices
- false_positive: patterns that were flagged but are intentionally used
- tech_stack: languages, frameworks, tools, versions in use
- team_preference: how the team prefers findings presented

Rules:
- Each fact must be a single, specific, actionable statement
- Do NOT extract generic programming advice
- Focus on what's SPECIFIC to THIS project/team
- Maximum 5 facts per review"""


class ExtractedFact(BaseModel, frozen=True):
    """A single fact extracted by the LLM."""

    content: str
    category: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.8)


class ExtractionResponse(BaseModel, frozen=True):
    """LLM response for fact extraction."""

    facts: list[ExtractedFact] = Field(default_factory=list)


def extract_facts_from_review(
    report: ReviewReport,
    llm_client: LLMClient,
) -> list[ExtractedFact]:
    """Extract reusable facts from a completed review."""
    if not report.agent_results:
        return []

    # Build context from review results
    parts: list[str] = []
    for result in report.agent_results:
        if not result.findings:
            continue
        parts.append(f"Agent: {result.agent_name}")
        for finding in result.findings[:5]:
            parts.append(f"  - [{finding.severity}] {finding.title}")
            if finding.file_path:
                parts.append(f"    File: {finding.file_path}")
            if finding.suggestion:
                parts.append(f"    Suggestion: {finding.suggestion[:100]}")

    if not parts:
        return []

    user_prompt = "Review findings:\n" + "\n".join(parts)

    try:
        response = llm_client.complete(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_model=ExtractionResponse,
        )
        logger.debug(
            "facts_extracted",
            count=len(response.facts),
        )
        return list(response.facts)
    except Exception:
        logger.debug("fact_extraction_failed", exc_info=True)
        return []


def save_extracted_facts(
    facts: list[ExtractedFact],
    store: FactStore,
    source: str = "review",
) -> int:
    """Save extracted facts to the persistent store. Returns count saved."""
    count = 0
    for fact in facts:
        store.add_fact(
            content=fact.content,
            category=fact.category,
            confidence=fact.confidence,
            source=source,
        )
        count += 1
    return count
