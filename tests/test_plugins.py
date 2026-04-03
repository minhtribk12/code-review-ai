"""Tests for the plugin system."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from code_review_agent.interactive.plugins import (
    load_all_plugins,
    load_plugin,
)


class TestLoadPlugin:
    """Test loading individual plugins."""

    def test_valid_plugin(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "my-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.yaml").write_text(
            "name: my-plugin\nversion: 1.0.0\nauthor: test\ndescription: A test plugin\n"
        )
        result = load_plugin(plugin_dir)
        assert result is not None
        assert result.manifest.name == "my-plugin"
        assert result.manifest.version == "1.0.0"

    def test_missing_manifest(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "bad-plugin"
        plugin_dir.mkdir()
        assert load_plugin(plugin_dir) is None

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "bad-yaml"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.yaml").write_text(": : : invalid")
        assert load_plugin(plugin_dir) is None

    def test_plugin_with_commands(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "cmd-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.yaml").write_text("name: cmd-plugin\nversion: 0.1\nauthor: x\n")
        cmd_dir = plugin_dir / "commands"
        cmd_dir.mkdir()
        (cmd_dir / "review.md").write_text(
            "---\nname: review\ndescription: run review\n---\nreview\n"
        )
        result = load_plugin(plugin_dir)
        assert result is not None
        assert len(result.commands) == 1
        assert result.commands[0].name == "review"

    def test_plugin_with_agents(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "agent-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.yaml").write_text("name: agent-plugin\nversion: 0.1\nauthor: x\n")
        agents_dir = plugin_dir / "agents"
        agents_dir.mkdir()
        (agents_dir / "custom.yaml").write_text("name: custom\n")
        result = load_plugin(plugin_dir)
        assert result is not None
        assert len(result.agent_files) == 1


class TestLoadAllPlugins:
    """Test loading plugins from directories."""

    def test_empty_dirs(self, tmp_path: Path) -> None:
        registry = load_all_plugins(
            user_dir=tmp_path / "user",
            project_dir=tmp_path / "project",
        )
        assert registry.plugin_count == 0

    def test_loads_from_user_dir(self, tmp_path: Path) -> None:
        user_dir = tmp_path / "plugins"
        user_dir.mkdir()
        plugin_dir = user_dir / "test-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.yaml").write_text("name: test\nversion: 0.1\nauthor: x\n")
        registry = load_all_plugins(user_dir=user_dir)
        assert registry.plugin_count == 1
        assert "test" in registry.plugins

    def test_project_overrides_user(self, tmp_path: Path) -> None:
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        p1 = user_dir / "shared"
        p1.mkdir()
        (p1 / "plugin.yaml").write_text("name: shared\nversion: 1.0\nauthor: user\n")

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        p2 = project_dir / "shared"
        p2.mkdir()
        (p2 / "plugin.yaml").write_text(
            "name: shared\nversion: 2.0\nauthor: project\ndescription: override\n"
        )

        registry = load_all_plugins(user_dir=user_dir, project_dir=project_dir)
        assert registry.plugin_count == 1
        assert registry.plugins["shared"].manifest.version == "2.0"


class TestPluginRegistry:
    """Test registry aggregation methods."""

    def test_get_all_commands(self, tmp_path: Path) -> None:
        user_dir = tmp_path / "plugins"
        user_dir.mkdir()
        plugin_dir = user_dir / "myplugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.yaml").write_text("name: myplugin\nversion: 0.1\nauthor: x\n")
        cmd_dir = plugin_dir / "commands"
        cmd_dir.mkdir()
        (cmd_dir / "lint.md").write_text("---\nname: lint\ndescription: run lint\n---\nlint\n")

        registry = load_all_plugins(user_dir=user_dir)
        cmds = registry.get_all_commands()
        assert "myplugin:lint" in cmds
