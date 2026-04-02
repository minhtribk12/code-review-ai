"""Tests for keybinding customization."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class TestKeybindings:
    """Test keybinding loading and resolution."""

    def test_defaults_when_no_file(self, tmp_path: Path) -> None:
        from code_review_agent.interactive.keybindings import DEFAULT_KEYBINDINGS, load_keybindings

        result = load_keybindings(path=tmp_path / "nonexistent.yaml")
        assert result == DEFAULT_KEYBINDINGS

    def test_yaml_overrides_specific_keys(self, tmp_path: Path) -> None:
        from code_review_agent.interactive.keybindings import DEFAULT_KEYBINDINGS, load_keybindings

        yaml_file = tmp_path / "keybindings.yaml"
        yaml_file.write_text("agent_selector: c-x\n")
        result = load_keybindings(path=yaml_file)
        assert result["agent_selector"] == "c-x"
        assert result["provider_selector"] == DEFAULT_KEYBINDINGS["provider_selector"]

    def test_invalid_yaml_falls_back(self, tmp_path: Path) -> None:
        from code_review_agent.interactive.keybindings import DEFAULT_KEYBINDINGS, load_keybindings

        yaml_file = tmp_path / "keybindings.yaml"
        yaml_file.write_text(": : : invalid yaml [[[")
        result = load_keybindings(path=yaml_file)
        assert result == DEFAULT_KEYBINDINGS

    def test_empty_yaml_returns_defaults(self, tmp_path: Path) -> None:
        from code_review_agent.interactive.keybindings import DEFAULT_KEYBINDINGS, load_keybindings

        yaml_file = tmp_path / "keybindings.yaml"
        yaml_file.write_text("")
        result = load_keybindings(path=yaml_file)
        assert result == DEFAULT_KEYBINDINGS

    def test_unknown_keys_ignored(self, tmp_path: Path) -> None:
        from code_review_agent.interactive.keybindings import DEFAULT_KEYBINDINGS, load_keybindings

        yaml_file = tmp_path / "keybindings.yaml"
        yaml_file.write_text("unknown_action: c-z\nagent_selector: c-x\n")
        result = load_keybindings(path=yaml_file)
        assert "unknown_action" not in result
        assert result["agent_selector"] == "c-x"
        assert len(result) == len(DEFAULT_KEYBINDINGS)

    def test_get_key_for_known_action(self) -> None:
        from code_review_agent.interactive.keybindings import (
            DEFAULT_KEYBINDINGS,
            get_key_for_action,
        )

        assert get_key_for_action(DEFAULT_KEYBINDINGS, "agent_selector") == "c-a"

    def test_get_key_for_unknown_action(self) -> None:
        from code_review_agent.interactive.keybindings import (
            DEFAULT_KEYBINDINGS,
            get_key_for_action,
        )

        assert get_key_for_action(DEFAULT_KEYBINDINGS, "nonexistent") == ""
