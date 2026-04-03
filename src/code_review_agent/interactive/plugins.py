"""Plugin system: declarative plugins contributing hooks, commands, and agents.

Plugins live in ~/.cra/plugins/<name>/ or .cra/plugins/<name>/ with a
plugin.yaml manifest. They are loaded at startup and contribute:
- hooks (merged into global hook list)
- slash commands (available as /plugin-name:command)
- custom agent YAML definitions
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
import yaml

if TYPE_CHECKING:
    from code_review_agent.interactive.hooks import HookConfig
    from code_review_agent.interactive.slash_commands import SlashCommand

logger = structlog.get_logger(__name__)

_USER_PLUGINS_DIR = Path("~/.cra/plugins").expanduser()
_PROJECT_PLUGINS_DIR = Path(".cra/plugins")


@dataclass(frozen=True)
class PluginManifest:
    """Parsed plugin.yaml manifest."""

    name: str
    version: str
    author: str
    description: str
    path: Path


@dataclass(frozen=True)
class LoadedPlugin:
    """A fully loaded plugin with its contributions."""

    manifest: PluginManifest
    hooks: tuple[HookConfig, ...]
    commands: tuple[SlashCommand, ...]
    agent_files: tuple[Path, ...]


@dataclass
class PluginRegistry:
    """Registry of all loaded plugins."""

    plugins: dict[str, LoadedPlugin] = field(default_factory=dict)

    @property
    def plugin_count(self) -> int:
        return len(self.plugins)

    def get_all_hooks(self) -> list[HookConfig]:
        """Return all hooks from all plugins."""
        result: list[HookConfig] = []
        for plugin in self.plugins.values():
            result.extend(plugin.hooks)
        return result

    def get_all_commands(self) -> dict[str, SlashCommand]:
        """Return all commands from all plugins, prefixed with plugin name."""
        result: dict[str, SlashCommand] = {}
        for plugin in self.plugins.values():
            for cmd in plugin.commands:
                key = f"{plugin.manifest.name}:{cmd.name}"
                result[key] = cmd
        return result

    def get_all_agent_files(self) -> list[Path]:
        """Return all agent YAML file paths from all plugins."""
        result: list[Path] = []
        for plugin in self.plugins.values():
            result.extend(plugin.agent_files)
        return result


def load_plugin(plugin_dir: Path) -> LoadedPlugin | None:
    """Load a single plugin from its directory."""
    manifest_path = plugin_dir / "plugin.yaml"
    if not manifest_path.is_file():
        logger.debug(f"no plugin.yaml in {plugin_dir}")
        return None

    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning(f"failed to parse plugin.yaml in {plugin_dir}")
        return None

    if not isinstance(raw, dict):
        return None

    manifest = PluginManifest(
        name=str(raw.get("name", plugin_dir.name)),
        version=str(raw.get("version", "0.0.0")),
        author=str(raw.get("author", "unknown")),
        description=str(raw.get("description", "")),
        path=plugin_dir,
    )

    hooks = _load_plugin_hooks(plugin_dir, manifest.name)
    commands = _load_plugin_commands(plugin_dir, manifest.name)
    agent_files = _load_plugin_agents(plugin_dir)

    logger.debug(
        f"loaded plugin {manifest.name} v{manifest.version}: "
        f"{len(hooks)} hooks, {len(commands)} commands, {len(agent_files)} agents"
    )

    return LoadedPlugin(
        manifest=manifest,
        hooks=tuple(hooks),
        commands=tuple(commands),
        agent_files=tuple(agent_files),
    )


def load_all_plugins(
    user_dir: Path | None = None,
    project_dir: Path | None = None,
) -> PluginRegistry:
    """Load all plugins from user and project directories."""
    registry = PluginRegistry()

    for base_dir in [user_dir or _USER_PLUGINS_DIR, project_dir or _PROJECT_PLUGINS_DIR]:
        if not base_dir.is_dir():
            continue
        for plugin_dir in sorted(base_dir.iterdir()):
            if not plugin_dir.is_dir():
                continue
            plugin = load_plugin(plugin_dir)
            if plugin is not None:
                registry.plugins[plugin.manifest.name] = plugin

    return registry


def _load_plugin_hooks(plugin_dir: Path, plugin_name: str) -> list[HookConfig]:
    """Load hooks from a plugin's hooks.yaml."""
    from code_review_agent.interactive.hooks import load_hooks

    return load_hooks(user_dir=plugin_dir)


def _load_plugin_commands(plugin_dir: Path, plugin_name: str) -> list[SlashCommand]:
    """Load slash commands from a plugin's commands/ directory."""
    from code_review_agent.interactive.slash_commands import load_slash_commands

    commands_dir = plugin_dir / "commands"
    if not commands_dir.is_dir():
        return []
    cmds = load_slash_commands(user_dir=commands_dir)
    return list(cmds.values())


def _load_plugin_agents(plugin_dir: Path) -> list[Path]:
    """Find agent YAML definitions in a plugin's agents/ directory."""
    agents_dir = plugin_dir / "agents"
    if not agents_dir.is_dir():
        return []
    return sorted(agents_dir.glob("*.yaml"))
