"""Tests for project-level settings (.cra/config.yaml in repo root)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from code_review_agent.config_store import ConfigStore


class TestProjectConfig:
    """Test ConfigStore with project-level overlay."""

    def test_no_project_config(self, tmp_path: Path) -> None:
        user_yaml = tmp_path / "user" / "config.yaml"
        user_yaml.parent.mkdir()
        store = ConfigStore(path=user_yaml, project_path=tmp_path / "project" / "config.yaml")
        assert store.load_all_overrides() == {}

    def test_user_only(self, tmp_path: Path) -> None:
        user_yaml = tmp_path / "user" / "config.yaml"
        user_yaml.parent.mkdir()
        store = ConfigStore(path=user_yaml)
        store.set_value("llm_model", "test-model")
        assert store.load_all_overrides()["llm_model"] == "test-model"

    def test_project_overrides_user(self, tmp_path: Path) -> None:
        import yaml

        user_yaml = tmp_path / "user" / "config.yaml"
        user_yaml.parent.mkdir()
        project_yaml = tmp_path / "project" / "config.yaml"
        project_yaml.parent.mkdir()

        # Write user config
        user_yaml.write_text(yaml.safe_dump({"llm_model": "user-model", "log_level": "INFO"}))
        # Write project config
        project_yaml.write_text(yaml.safe_dump({"llm_model": "project-model"}))

        store = ConfigStore(path=user_yaml, project_path=project_yaml)
        overrides = store.load_all_overrides()
        assert overrides["llm_model"] == "project-model"
        assert overrides["log_level"] == "INFO"

    def test_set_value_writes_to_user_only(self, tmp_path: Path) -> None:
        import yaml

        user_yaml = tmp_path / "user" / "config.yaml"
        user_yaml.parent.mkdir()
        project_yaml = tmp_path / "project" / "config.yaml"
        project_yaml.parent.mkdir()
        project_yaml.write_text(yaml.safe_dump({"llm_model": "project-model"}))

        store = ConfigStore(path=user_yaml, project_path=project_yaml)
        store.set_value("log_level", "DEBUG")

        # User config should have the new value
        user_data = yaml.safe_load(user_yaml.read_text())
        assert user_data["log_level"] == "DEBUG"

        # Project config should be unchanged
        project_data = yaml.safe_load(project_yaml.read_text())
        assert "log_level" not in project_data

    def test_missing_project_dir_graceful(self, tmp_path: Path) -> None:
        user_yaml = tmp_path / "user" / "config.yaml"
        user_yaml.parent.mkdir()
        store = ConfigStore(
            path=user_yaml,
            project_path=tmp_path / "nonexistent" / "config.yaml",
        )
        assert store.load_all_overrides() == {}

    def test_load_project_overrides(self, tmp_path: Path) -> None:
        import yaml

        user_yaml = tmp_path / "user" / "config.yaml"
        user_yaml.parent.mkdir()
        project_yaml = tmp_path / "project" / "config.yaml"
        project_yaml.parent.mkdir()
        project_yaml.write_text(yaml.safe_dump({"llm_model": "project-model"}))

        store = ConfigStore(path=user_yaml, project_path=project_yaml)
        project_overrides = store.load_project_overrides()
        assert project_overrides == {"llm_model": "project-model"}
