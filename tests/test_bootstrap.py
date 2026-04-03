"""Tests for bootstrap state isolation and startup profiling."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from code_review_agent.interactive.bootstrap import (
    BootstrapState,
    StartupProfile,
)


class TestStartupProfile:
    def test_checkpoint_records_timing(self) -> None:
        profile = StartupProfile()
        profile.checkpoint("step1")
        profile.checkpoint("step2")
        assert len(profile.checkpoints) == 2
        assert profile.checkpoints[0].name == "step1"
        assert profile.checkpoints[1].elapsed_ms >= profile.checkpoints[0].elapsed_ms

    def test_total_ms(self) -> None:
        profile = StartupProfile()
        profile.checkpoint("done")
        assert profile.total_ms >= 0.0

    def test_empty_profile(self) -> None:
        profile = StartupProfile()
        assert profile.total_ms == 0.0
        assert profile.checkpoints == []

    def test_format_report(self) -> None:
        profile = StartupProfile()
        profile.checkpoint("imports")
        profile.checkpoint("settings")
        report = profile.format_report()
        assert "imports" in report
        assert "settings" in report
        assert "total" in report

    def test_save_to_file(self, tmp_path: Path) -> None:
        import json

        profile = StartupProfile()
        profile.checkpoint("test")
        path = tmp_path / "perf" / "startup.json"
        profile.save_to_file(path)
        assert path.exists()
        data = json.loads(path.read_text())
        assert "total_ms" in data
        assert len(data["checkpoints"]) == 1


class TestBootstrapState:
    def test_is_frozen(self) -> None:
        from unittest.mock import MagicMock

        state = BootstrapState(
            settings=MagicMock(),
            config_store=MagicMock(),
            secrets_store=MagicMock(),
            startup_profile=StartupProfile(),
        )
        with pytest.raises(AttributeError):
            state.settings = MagicMock()  # type: ignore[misc]


class TestBootstrap:
    def test_full_bootstrap(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("NVIDIA_API_KEY", "test-key")  # pragma: allowlist secret
        monkeypatch.setenv("LLM_PROVIDER", "nvidia")
        monkeypatch.chdir(tmp_path)

        from code_review_agent.config_store import ConfigStore, SecretsStore
        from code_review_agent.interactive.bootstrap import bootstrap

        state = bootstrap(
            config_store=ConfigStore(path=tmp_path / "config.yaml"),
            secrets_store=SecretsStore(path=tmp_path / "secrets.env"),
        )
        assert state.settings is not None
        assert state.startup_profile.total_ms >= 0
        assert len(state.startup_profile.checkpoints) >= 3
