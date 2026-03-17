"""Custom YAML-defined agent loader.

Discovers and loads agent definitions from YAML files in configurable
directories. Each YAML file defines an agent's name, system prompt,
and optional metadata. Dynamic ``BaseAgent`` subclasses are created
at runtime and registered in the global agent registry.

Discovery order (later overrides earlier):
1. Project-local: ``.cra/agents/`` in the current working directory
2. User-global: ``~/.cra/agents/`` (or custom ``custom_agents_dir``)
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
import yaml
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from code_review_agent.agents.base import BaseAgent

logger = structlog.get_logger(__name__)


class CustomAgentSpec(BaseModel):
    """Validated schema for a YAML agent definition.

    Uses ``extra="ignore"`` so future YAML fields (e.g. ``model``,
    ``temperature``) do not break older tool versions.
    """

    model_config = {"frozen": True, "extra": "ignore"}

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    system_prompt: str = Field(min_length=1)
    description: str = ""
    priority: int = Field(default=100, ge=0)
    enabled: bool = True
    file_patterns: list[str] | None = None


def discover_agent_dirs(custom_agents_dir: str) -> list[Path]:
    """Return existing agent directories in discovery order.

    Order: project-local ``.cra/agents/`` first, then the user-global
    directory. Non-existent directories are silently skipped.
    """
    candidates = [
        Path.cwd() / ".cra" / "agents",
        Path(custom_agents_dir).expanduser(),
    ]
    # Deduplicate (if CWD/.cra/agents == expanded custom dir)
    seen: set[Path] = set()
    result: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in seen and resolved.is_dir():
            seen.add(resolved)
            result.append(resolved)
    return result


def load_custom_agents(
    directories: list[Path],
) -> dict[str, type[BaseAgent]]:
    """Load custom agents from YAML files in the given directories.

    Directories are processed in order. Within each directory, files are
    sorted alphabetically for deterministic load order. Later directories
    override earlier ones (and built-in agents) by name.

    Invalid YAML files are skipped with a warning.
    """
    agents: dict[str, type[BaseAgent]] = {}
    for directory in directories:
        agents.update(_load_yaml_agents(directory))
    return agents


def matches_diff_files(
    file_patterns: list[str] | None,
    filenames: list[str],
) -> bool:
    """Check if any filename matches the agent's file patterns.

    Returns ``True`` if ``file_patterns`` is ``None`` (matches all files)
    or if any filename matches any pattern.
    """
    if file_patterns is None:
        return True
    return any(fnmatch(filename, pattern) for filename in filenames for pattern in file_patterns)


def _load_yaml_agents(directory: Path) -> dict[str, type[BaseAgent]]:
    """Load all YAML agent definitions from a single directory."""
    agents: dict[str, type[BaseAgent]] = {}
    yaml_files = sorted(
        [f for f in directory.iterdir() if f.suffix in (".yaml", ".yml")],
        key=lambda f: f.name,
    )

    for yaml_file in yaml_files:
        try:
            spec = _parse_yaml_file(yaml_file)
        except Exception:
            logger.warning(
                "skipping invalid agent YAML",
                file=str(yaml_file),
            )
            continue

        if not spec.enabled:
            logger.debug(
                "skipping disabled custom agent",
                agent=spec.name,
                file=str(yaml_file),
            )
            continue

        try:
            agent_cls = _create_agent_class(spec)
        except Exception:
            logger.warning(
                "failed to create agent class from YAML",
                agent=spec.name,
                file=str(yaml_file),
            )
            continue

        agents[spec.name] = agent_cls
        logger.info(
            "loaded custom agent",
            agent=spec.name,
            file=str(yaml_file),
            priority=spec.priority,
        )

    return agents


def _parse_yaml_file(path: Path) -> CustomAgentSpec:
    """Parse and validate a single YAML agent file."""
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        msg = f"expected a YAML mapping, got {type(data).__name__}"
        raise ValueError(msg)
    return CustomAgentSpec.model_validate(data)


def _create_agent_class(spec: CustomAgentSpec) -> type[BaseAgent]:
    """Dynamically create a BaseAgent subclass from a CustomAgentSpec.

    Handles override of existing agents by temporarily removing the
    name from ``_registered_names`` so ``__init_subclass__`` validation
    passes. If class creation fails, the original registration is restored.
    """
    from code_review_agent.agents.base import BaseAgent

    pascal_name = _to_pascal_case(spec.name) + "CustomAgent"

    is_override = spec.name in BaseAgent._registered_names
    if is_override:
        BaseAgent._registered_names.discard(spec.name)
        logger.warning(
            "overriding existing agent",
            agent=spec.name,
        )

    try:
        cls = type(
            pascal_name,
            (BaseAgent,),
            {
                "name": spec.name,
                "system_prompt": spec.system_prompt,
                "priority": spec.priority,
                "_custom_description": spec.description,
                "_file_patterns": spec.file_patterns,
            },
        )
    except TypeError:
        if is_override:
            BaseAgent._registered_names.add(spec.name)
        raise

    return cls


def _to_pascal_case(snake: str) -> str:
    """Convert a snake_case string to PascalCase."""
    return "".join(word.capitalize() for word in snake.split("_"))
