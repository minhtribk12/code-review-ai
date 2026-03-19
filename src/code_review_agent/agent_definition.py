"""Unified agent definition model and layered resolution.

Provides a single ``AgentDefinition`` type that represents an agent
from any source (built-in, YAML, or database). The ``resolve_all_agents``
function merges all three layers with DB > YAML > built-in precedence.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from code_review_agent.config import Settings
    from code_review_agent.storage import ReviewStorage


class AgentSource(StrEnum):
    """Origin of an agent definition."""

    BUILTIN = "built-in"
    YAML = "yaml"
    DB = "db"


class AgentDefinition(BaseModel):
    """Unified view of an agent from any source layer."""

    model_config = {"frozen": True}

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    system_prompt: str = Field(min_length=1)
    description: str = ""
    priority: int = Field(default=100, ge=0)
    enabled: bool = True
    file_patterns: list[str] | None = None
    source: AgentSource = AgentSource.BUILTIN


def resolve_all_agents(
    settings: Settings,
    storage: ReviewStorage,
) -> list[AgentDefinition]:
    """Merge agents from all layers: built-in < YAML < DB.

    Returns all agents sorted by priority then name. Later layers
    override earlier ones by name.
    """
    agents: dict[str, AgentDefinition] = {}

    # Layer 1: built-in agents
    from code_review_agent.agents import AGENT_REGISTRY, BUILTIN_AGENT_NAMES

    for name in BUILTIN_AGENT_NAMES:
        agent_cls = AGENT_REGISTRY.get(name)
        if agent_cls is None:
            continue
        agents[name] = AgentDefinition(
            name=name,
            system_prompt=agent_cls.system_prompt,
            description=f"Specialized {name} reviewer",
            priority=getattr(agent_cls, "priority", 100),
            source=AgentSource.BUILTIN,
        )

    # Layer 2: YAML custom agents
    from code_review_agent.agents import CUSTOM_AGENT_NAMES

    for name in CUSTOM_AGENT_NAMES:
        agent_cls = AGENT_REGISTRY.get(name)
        if agent_cls is None:
            continue
        agents[name] = AgentDefinition(
            name=name,
            system_prompt=agent_cls.system_prompt,
            description=getattr(agent_cls, "_custom_description", "") or "",
            priority=getattr(agent_cls, "priority", 100),
            file_patterns=getattr(agent_cls, "_file_patterns", None),
            source=AgentSource.YAML,
        )

    # Layer 3: database agents (highest precedence)
    db_agents = storage.load_all_agents()
    for row in db_agents:
        agents[row["name"]] = AgentDefinition(
            name=row["name"],
            system_prompt=row["system_prompt"],
            description=row.get("description", ""),
            priority=row.get("priority", 100),
            enabled=row.get("enabled", True),
            file_patterns=row.get("file_patterns"),
            source=AgentSource.DB,
        )

    return sorted(agents.values(), key=lambda a: (a.priority, a.name))
