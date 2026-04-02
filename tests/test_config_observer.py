"""Tests for reactive config observer pattern."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from code_review_agent.interactive.session import SessionState


@pytest.fixture
def observer_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SessionState:
    """Real SessionState with isolated config store."""
    monkeypatch.setenv("NVIDIA_API_KEY", "sk-test-fake-key")  # pragma: allowlist secret
    monkeypatch.setenv("LLM_PROVIDER", "nvidia")
    from code_review_agent.config import Settings
    from code_review_agent.config_store import ConfigStore

    return SessionState(
        settings=Settings(),
        config_store=ConfigStore(path=tmp_path / "config.yaml"),
    )


class TestConfigObserver:
    """Test the config listener mechanism on SessionState."""

    def test_add_and_fire_listener(self, observer_session: SessionState) -> None:
        calls: list[str] = []
        observer_session.add_config_listener(lambda: calls.append("fired"))
        observer_session.invalidate_settings_cache()
        assert calls == ["fired"]

    def test_multiple_listeners_fire_in_order(self, observer_session: SessionState) -> None:
        calls: list[int] = []
        observer_session.add_config_listener(lambda: calls.append(1))
        observer_session.add_config_listener(lambda: calls.append(2))
        observer_session.add_config_listener(lambda: calls.append(3))
        observer_session.invalidate_settings_cache()
        assert calls == [1, 2, 3]

    def test_remove_listener(self, observer_session: SessionState) -> None:
        calls: list[str] = []

        def cb() -> None:
            calls.append("x")

        observer_session.add_config_listener(cb)
        observer_session.remove_config_listener(cb)
        observer_session.invalidate_settings_cache()
        assert calls == []

    def test_remove_nonexistent_listener_is_noop(self, observer_session: SessionState) -> None:
        observer_session.remove_config_listener(lambda: None)  # should not raise

    def test_exception_does_not_break_others(self, observer_session: SessionState) -> None:
        calls: list[str] = []

        def bad() -> None:
            msg = "boom"
            raise RuntimeError(msg)

        observer_session.add_config_listener(bad)
        observer_session.add_config_listener(lambda: calls.append("ok"))
        observer_session.invalidate_settings_cache()
        assert calls == ["ok"]

    def test_cache_cleared_before_listeners(self, observer_session: SessionState) -> None:
        cache_state: list[bool] = []
        observer_session.config_overrides["llm_temperature"] = "0.5"
        _ = observer_session.effective_settings
        observer_session.add_config_listener(
            lambda: cache_state.append(observer_session._effective_settings_cache is None)
        )
        observer_session.invalidate_settings_cache()
        assert cache_state == [True]
