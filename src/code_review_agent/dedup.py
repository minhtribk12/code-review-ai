"""Cross-agent finding deduplication.

Removes duplicate findings reported by multiple agents for the same issue.
Strategy is configurable via DEDUP_STRATEGY setting.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from enum import StrEnum

import structlog

from code_review_agent.models import AgentResult, Finding, Severity

logger = structlog.get_logger(__name__)


def _get_agent_priority(agent_name: str) -> int:
    """Return the dedup priority for an agent (lower = higher priority).

    Reads from BaseAgent._priority_registry, which is populated by
    ``__init_subclass__``. Unknown agents default to 99.
    """
    from code_review_agent.agents.base import BaseAgent

    return BaseAgent._priority_registry.get(agent_name, 99)


_SEVERITY_RANK: dict[Severity, int] = {
    Severity.CRITICAL: 0,
    Severity.HIGH: 1,
    Severity.MEDIUM: 2,
    Severity.LOW: 3,
}

_SIMILAR_THRESHOLD = 0.6


class DedupStrategy(StrEnum):
    """Strategy for deduplicating findings across agents."""

    EXACT = "exact"
    LOCATION = "location"
    SIMILAR = "similar"
    DISABLED = "disabled"


def deduplicate_agent_results(
    agent_results: list[AgentResult],
    strategy: DedupStrategy,
) -> list[AgentResult]:
    """Remove duplicate findings across agents and return updated results.

    Each agent's findings are checked against all other agents' findings.
    When duplicates are found, the finding with the highest severity (and
    agent priority as tiebreaker) survives. The surviving finding stays
    with its original agent.

    Returns a new list of AgentResult with deduplicated findings.
    """
    if strategy == DedupStrategy.DISABLED:
        return agent_results

    # Collect all (finding, agent_name) pairs
    all_tagged: list[tuple[Finding, str]] = []
    for result in agent_results:
        for finding in result.findings:
            all_tagged.append((finding, result.agent_name))

    if not all_tagged:
        return agent_results

    # Group duplicates
    kept: set[int] = set()
    removed: set[int] = set()

    for i, (finding_a, agent_a) in enumerate(all_tagged):
        if i in removed:
            continue
        for j in range(i + 1, len(all_tagged)):
            if j in removed:
                continue
            finding_b, agent_b = all_tagged[j]

            if not _is_duplicate(finding_a, finding_b, strategy):
                continue

            # Decide which to keep: lower rank = higher priority
            survivor = _pick_survivor(finding_a, agent_a, finding_b, agent_b)
            if survivor == i:
                removed.add(j)
            else:
                removed.add(i)
                break  # finding_a is removed, stop comparing it

        if i not in removed:
            kept.add(i)

    original_count = len(all_tagged)
    dedup_count = len(removed)

    if dedup_count > 0:
        logger.debug(
            "deduplicated findings across agents",
            strategy=strategy.value,
            original=original_count,
            removed=dedup_count,
            remaining=original_count - dedup_count,
        )

    # Rebuild agent results with only kept findings
    surviving_by_agent: dict[str, list[Finding]] = {}
    for i, (finding, agent_name) in enumerate(all_tagged):
        if i not in removed:
            surviving_by_agent.setdefault(agent_name, []).append(finding)

    updated: list[AgentResult] = []
    for result in agent_results:
        new_findings = surviving_by_agent.get(result.agent_name, [])
        updated.append(
            AgentResult(
                agent_name=result.agent_name,
                findings=new_findings,
                summary=result.summary,
                execution_time_seconds=result.execution_time_seconds,
                status=result.status,
                error_message=result.error_message,
            )
        )

    return updated


def _is_duplicate(a: Finding, b: Finding, strategy: DedupStrategy) -> bool:
    """Check if two findings are duplicates under the given strategy."""
    if strategy == DedupStrategy.EXACT:
        return a.file_path == b.file_path and a.line_number == b.line_number and a.title == b.title

    if strategy == DedupStrategy.LOCATION:
        return a.file_path == b.file_path and a.line_number == b.line_number

    if strategy == DedupStrategy.SIMILAR:
        if a.file_path != b.file_path or a.line_number != b.line_number:
            return False
        return _title_similarity(a.title, b.title) >= _SIMILAR_THRESHOLD

    return False


def _title_similarity(a: str, b: str) -> float:
    """Return similarity ratio between two titles (0.0 to 1.0)."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _pick_survivor(
    finding_a: Finding,
    agent_a: str,
    finding_b: Finding,
    agent_b: str,
) -> int:
    """Return 0 if finding_a survives, 1 if finding_b survives.

    Highest severity wins. Agent priority breaks ties.
    """
    rank_a = _SEVERITY_RANK.get(finding_a.severity, 99)
    rank_b = _SEVERITY_RANK.get(finding_b.severity, 99)

    if rank_a != rank_b:
        return 0 if rank_a < rank_b else 1

    # Tiebreaker: agent priority (lower = higher priority)
    priority_a = _get_agent_priority(agent_a)
    priority_b = _get_agent_priority(agent_b)
    return 0 if priority_a <= priority_b else 1
