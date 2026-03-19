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

DB_AGENT_NAMES: set[str] = set()

_custom_agents_registered = False


def register_custom_agents(settings: Settings) -> None:
    """Discover and register custom YAML-defined and DB-stored agents.

    Updates ``AGENT_REGISTRY``, ``ALL_AGENT_NAMES``,
    ``CUSTOM_AGENT_NAMES``, and ``DB_AGENT_NAMES`` in place.
    Safe to call multiple times -- subsequent calls are no-ops.
    """
    global _custom_agents_registered
    if _custom_agents_registered:
        return

    from code_review_agent.agent_loader import (
        CustomAgentSpec,
        _create_agent_class,
        discover_agent_dirs,
        load_custom_agents,
    )

    # Layer 1: YAML agents
    directories = discover_agent_dirs(settings.custom_agents_dir)
    if directories:
        custom_agents = load_custom_agents(directories)
        for name, agent_cls in custom_agents.items():
            if name in AGENT_REGISTRY and name in BUILTIN_AGENT_NAMES:
                logger.warning(
                    "custom agent overrides built-in agent",
                    agent=name,
                )
            AGENT_REGISTRY[name] = agent_cls
            CUSTOM_AGENT_NAMES.add(name)

        if custom_agents:
            logger.debug(
                "custom agents registered",
                count=len(custom_agents),
                names=list(custom_agents.keys()),
            )

    # Layer 2: database agents (highest precedence)
    try:
        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(settings.history_db_path)
        db_agents = storage.load_all_agents()
        for row in db_agents:
            if not row.get("enabled", True):
                continue
            spec = CustomAgentSpec(
                name=row["name"],
                system_prompt=row["system_prompt"],
                description=row.get("description", ""),
                priority=row.get("priority", 100),
                file_patterns=row.get("file_patterns"),
            )
            agent_cls = _create_agent_class(spec)
            AGENT_REGISTRY[spec.name] = agent_cls
            DB_AGENT_NAMES.add(spec.name)

        if db_agents:
            logger.debug(
                "db agents registered",
                count=len(DB_AGENT_NAMES),
                names=list(DB_AGENT_NAMES),
            )
    except Exception:
        logger.debug("failed to load db agents", exc_info=True)

    ALL_AGENT_NAMES.clear()
    ALL_AGENT_NAMES.extend(AGENT_REGISTRY.keys())
    _custom_agents_registered = True


def reload_agents(settings: Settings) -> None:
    """Reset all custom/DB agents and re-register from scratch.

    Call after saving agent edits in the browser to refresh the
    in-memory registry.
    """
    reset_custom_agents()
    register_custom_agents(settings)


def reset_custom_agents() -> None:
    """Remove all custom and DB agents and reset registration state."""
    global _custom_agents_registered
    from code_review_agent.agents.base import BaseAgent

    for name in list(CUSTOM_AGENT_NAMES | DB_AGENT_NAMES):
        AGENT_REGISTRY.pop(name, None)
        BaseAgent._registered_names.discard(name)
        BaseAgent._priority_registry.pop(name, None)

    CUSTOM_AGENT_NAMES.clear()
    DB_AGENT_NAMES.clear()
    ALL_AGENT_NAMES.clear()
    ALL_AGENT_NAMES.extend(AGENT_REGISTRY.keys())
    _custom_agents_registered = False
