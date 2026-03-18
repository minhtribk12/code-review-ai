from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from code_review_agent.agents.performance import PerformanceAgent
from code_review_agent.agents.security import SecurityAgent
from code_review_agent.agents.style import StyleAgent
from code_review_agent.agents.test_coverage import TestCoverageAgent

if TYPE_CHECKING:
    from code_review_agent.agents.base import BaseAgent
    from code_review_agent.config import Settings

logger = structlog.get_logger(__name__)

__all__ = ["PerformanceAgent", "SecurityAgent", "StyleAgent", "TestCoverageAgent"]

AGENT_REGISTRY: dict[str, type[BaseAgent]] = {
    "security": SecurityAgent,
    "performance": PerformanceAgent,
    "style": StyleAgent,
    "test_coverage": TestCoverageAgent,
}

ALL_AGENT_NAMES: list[str] = list(AGENT_REGISTRY.keys())

BUILTIN_AGENT_NAMES: frozenset[str] = frozenset(AGENT_REGISTRY.keys())

CUSTOM_AGENT_NAMES: set[str] = set()

_custom_agents_registered = False


def register_custom_agents(settings: Settings) -> None:
    """Discover and register custom YAML-defined agents.

    Updates ``AGENT_REGISTRY``, ``ALL_AGENT_NAMES``, and
    ``CUSTOM_AGENT_NAMES`` in place. Safe to call multiple times --
    subsequent calls are no-ops.
    """
    global _custom_agents_registered
    if _custom_agents_registered:
        return

    from code_review_agent.agent_loader import discover_agent_dirs, load_custom_agents

    directories = discover_agent_dirs(settings.custom_agents_dir)
    if not directories:
        _custom_agents_registered = True
        return

    custom_agents = load_custom_agents(directories)

    for name, agent_cls in custom_agents.items():
        if name in AGENT_REGISTRY and name in BUILTIN_AGENT_NAMES:
            logger.warning(
                "custom agent overrides built-in agent",
                agent=name,
            )
        AGENT_REGISTRY[name] = agent_cls
        CUSTOM_AGENT_NAMES.add(name)

    ALL_AGENT_NAMES.clear()
    ALL_AGENT_NAMES.extend(AGENT_REGISTRY.keys())

    if custom_agents:
        logger.info(
            "custom agents registered",
            count=len(custom_agents),
            names=list(custom_agents.keys()),
        )

    _custom_agents_registered = True


def reset_custom_agents() -> None:
    """Remove all custom agents and reset registration state.

    Intended for testing only.
    """
    global _custom_agents_registered
    from code_review_agent.agents.base import BaseAgent

    for name in list(CUSTOM_AGENT_NAMES):
        AGENT_REGISTRY.pop(name, None)
        BaseAgent._registered_names.discard(name)
        BaseAgent._priority_registry.pop(name, None)

    CUSTOM_AGENT_NAMES.clear()
    ALL_AGENT_NAMES.clear()
    ALL_AGENT_NAMES.extend(AGENT_REGISTRY.keys())
    _custom_agents_registered = False
