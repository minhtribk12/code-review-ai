"""Learning from dismissed findings.

Queries triage history to build suppression patterns. Findings that have
been dismissed (FALSE_POSITIVE or IGNORED) 2+ times by the same agent are
added to a suppression list injected into agent prompts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from code_review_agent.storage import ReviewStorage

logger = structlog.get_logger(__name__)

_MIN_DISMISS_COUNT = 2


@dataclass(frozen=True)
class SuppressionPattern:
    """A finding pattern that has been repeatedly dismissed."""

    title: str
    agent_name: str
    dismiss_count: int
    triage_action: str


def load_suppression_patterns(
    storage: ReviewStorage,
    *,
    min_count: int = _MIN_DISMISS_COUNT,
) -> list[SuppressionPattern]:
    """Query triage history for repeatedly dismissed finding patterns.

    Returns patterns dismissed >= ``min_count`` times as FALSE_POSITIVE
    or IGNORED by the same agent.
    """
    try:
        rows = storage.query_dismissed_patterns(min_count=min_count)
    except Exception:
        logger.debug("failed to load suppression patterns", exc_info=True)
        return []
    return [
        SuppressionPattern(
            title=row["title"],
            agent_name=row["agent_name"],
            dismiss_count=row["dismiss_count"],
            triage_action=row["triage_action"],
        )
        for row in rows
    ]


def build_suppression_prompt(patterns: list[SuppressionPattern]) -> str:
    """Build a prompt section listing suppressed patterns for an agent.

    Returns empty string if no patterns match the agent.
    """
    if not patterns:
        return ""

    lines = [
        "Previously dismissed patterns (do not flag unless clearly different):",
    ]
    for p in patterns:
        lines.append(f'  - "{p.title}" (dismissed {p.dismiss_count}x as {p.triage_action})')
    return "\n".join(lines)


def filter_patterns_for_agent(
    patterns: list[SuppressionPattern],
    agent_name: str,
) -> list[SuppressionPattern]:
    """Filter suppression patterns to those relevant for a specific agent."""
    return [p for p in patterns if p.agent_name == agent_name]
