from __future__ import annotations

from typing import TYPE_CHECKING

from code_review_agent.agents.performance import PerformanceAgent
from code_review_agent.agents.security import SecurityAgent
from code_review_agent.agents.style import StyleAgent
from code_review_agent.agents.test_coverage import TestCoverageAgent

if TYPE_CHECKING:
    from code_review_agent.agents.base import BaseAgent

__all__ = ["PerformanceAgent", "SecurityAgent", "StyleAgent", "TestCoverageAgent"]

AGENT_REGISTRY: dict[str, type[BaseAgent]] = {
    "security": SecurityAgent,
    "performance": PerformanceAgent,
    "style": StyleAgent,
    "test_coverage": TestCoverageAgent,
}

ALL_AGENT_NAMES: list[str] = list(AGENT_REGISTRY.keys())
