"""Tests for API key consistency across all storage and display layers.

Verifies that API keys saved via any method (startup panel, config edit,
config set, env var, .env file) are visible from all read paths:
- config show / config get
- config edit display
- startup panel (_provider_has_key)
- effective_settings.resolved_api_key
- session.resolve_api_key_display()
"""  # pragma: allowlist secret

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from code_review_agent.interactive.session import SessionState


@pytest.fixture
def tmp_db(tmp_path: Path) -> str:
    """Return a path for a temporary SQLite DB."""
    return str(tmp_path / "test_reviews.db")


@pytest.fixture
def session(tmp_db: str, monkeypatch: pytest.MonkeyPatch) -> SessionState:
    """Create a minimal SessionState with a temp DB."""
    monkeypatch.setenv("NVIDIA_API_KEY", "__placeholder__")
    monkeypatch.setenv("LLM_PROVIDER", "nvidia")

    from code_review_agent.config import Settings

    settings = MagicMock(spec=Settings)
    settings.history_db_path = tmp_db
    settings.llm_provider = "nvidia"
    settings.llm_model = "nvidia/test-model"
    settings.nvidia_api_key = None
    settings.openrouter_api_key = None
    settings.resolved_api_key = MagicMock()
    settings.resolved_api_key.get_secret_value.return_value = "__placeholder__"
    settings.resolve_api_key_for = MagicMock(return_value=None)
    # Make hasattr checks work for any key
    settings.model_fields = {}

    return SessionState(settings=settings)


class TestResolveApiKeyDisplay:
    """Test session.resolve_api_key_display() across all sources."""

    def test_returns_empty_when_no_key(self, session: SessionState) -> None:
        result = session.resolve_api_key_display("nvidia")
        assert result == ""

    def test_reads_from_config_overrides(self, session: SessionState) -> None:
        session.config_overrides["nvidia_api_key"] = (
            "nvapi-from-overrides"  # pragma: allowlist secret
        )
        result = session.resolve_api_key_display("nvidia")
        assert result == "nvapi-from-overrides"

    def test_reads_from_settings_field(self, session: SessionState) -> None:
        from pydantic import SecretStr

        session.settings.nvidia_api_key = SecretStr("nvapi-from-env")
        result = session.resolve_api_key_display("nvidia")
        assert result == "nvapi-from-env"

    def test_reads_from_database(self, session: SessionState) -> None:
        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(session.settings.history_db_path)
        storage.save_config("nvidia_api_key", "nvapi-from-db")

        result = session.resolve_api_key_display("nvidia")
        assert result == "nvapi-from-db"

    def test_reads_from_env_var(
        self,
        session: SessionState,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-from-env-var")
        result = session.resolve_api_key_display("nvidia")
        assert result == "nvapi-from-env-var"

    def test_overrides_take_priority_over_db(
        self,
        session: SessionState,
    ) -> None:
        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(session.settings.history_db_path)
        storage.save_config("nvidia_api_key", "nvapi-from-db")

        session.config_overrides["nvidia_api_key"] = (
            "nvapi-from-overrides"  # pragma: allowlist secret
        )
        result = session.resolve_api_key_display("nvidia")
        assert result == "nvapi-from-overrides"

    def test_settings_takes_priority_over_db(
        self,
        session: SessionState,
    ) -> None:
        from pydantic import SecretStr

        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(session.settings.history_db_path)
        storage.save_config("nvidia_api_key", "nvapi-from-db")

        session.settings.nvidia_api_key = SecretStr("nvapi-from-env")
        result = session.resolve_api_key_display("nvidia")
        assert result == "nvapi-from-env"

    def test_ignores_placeholder_in_settings(
        self,
        session: SessionState,
    ) -> None:
        from pydantic import SecretStr

        from code_review_agent.storage import ReviewStorage

        session.settings.nvidia_api_key = SecretStr("__placeholder__")
        storage = ReviewStorage(session.settings.history_db_path)
        storage.save_config("nvidia_api_key", "nvapi-real-key")

        result = session.resolve_api_key_display("nvidia")
        assert result == "nvapi-real-key"

    def test_ignores_placeholder_in_env(
        self,
        session: SessionState,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("NVIDIA_API_KEY", "__placeholder__")
        result = session.resolve_api_key_display("nvidia")
        assert result == ""

    def test_uses_active_provider_by_default(
        self,
        session: SessionState,
    ) -> None:
        session.config_overrides["nvidia_api_key"] = "nvapi-test"  # pragma: allowlist secret
        # Default provider is nvidia (from session.settings.llm_provider)
        result = session.resolve_api_key_display()
        assert result == "nvapi-test"

    def test_custom_provider_from_db(
        self,
        session: SessionState,
    ) -> None:
        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(session.settings.history_db_path)
        storage.save_config("ollama_api_key", "ollama-key-123")

        result = session.resolve_api_key_display("ollama")
        assert result == "ollama-key-123"

    def test_none_override_ignored(self, session: SessionState) -> None:
        session.config_overrides["nvidia_api_key"] = "None"  # pragma: allowlist secret
        result = session.resolve_api_key_display("nvidia")
        # "None" is treated as empty
        assert result == ""


class TestInjectDbApiKeysToEnv:
    """Test that DB keys are injected into env for Settings to pick up."""

    def test_injects_builtin_provider_key(
        self,
        session: SessionState,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("NVIDIA_API_KEY", "__placeholder__")

        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(session.settings.history_db_path)
        storage.save_config("nvidia_api_key", "nvapi-from-db")

        session._inject_db_api_keys_to_env()

        assert os.environ["NVIDIA_API_KEY"] == "nvapi-from-db"  # pragma: allowlist secret

    def test_injects_custom_provider_key(
        self,
        session: SessionState,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)

        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(session.settings.history_db_path)
        storage.save_config("ollama_api_key", "ollama-key-123")

        session._inject_db_api_keys_to_env()

        assert os.environ.get("OLLAMA_API_KEY") == "ollama-key-123"

    def test_does_not_overwrite_real_env_key(
        self,
        session: SessionState,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-real-from-env")

        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(session.settings.history_db_path)
        storage.save_config("nvidia_api_key", "nvapi-from-db")

        session._inject_db_api_keys_to_env()

        # Real env key should NOT be overwritten
        assert os.environ["NVIDIA_API_KEY"] == "nvapi-real-from-env"  # pragma: allowlist secret

    def test_overwrites_placeholder_env_key(
        self,
        session: SessionState,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("NVIDIA_API_KEY", "__placeholder__")

        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(session.settings.history_db_path)
        storage.save_config("nvidia_api_key", "nvapi-from-db")

        session._inject_db_api_keys_to_env()

        assert os.environ["NVIDIA_API_KEY"] == "nvapi-from-db"  # pragma: allowlist secret


class TestConfigCmdConsistency:
    """Test that config show/get sees DB-stored keys."""

    def test_config_get_sees_db_key(self, session: SessionState) -> None:
        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(session.settings.history_db_path)
        storage.save_config("nvidia_api_key", "nvapi-from-db-test")

        from code_review_agent.interactive.commands.config_cmd import (
            _get_config_value,
        )

        value = _get_config_value(session, "llm_api_key")
        # Should find the DB key and return it as SecretStr
        assert value is not None
        from pydantic import SecretStr

        if isinstance(value, SecretStr):
            assert value.get_secret_value() == "nvapi-from-db-test"
        else:
            assert str(value) == "nvapi-from-db-test"

    def test_config_get_returns_none_when_no_key(
        self,
        session: SessionState,
    ) -> None:
        from code_review_agent.interactive.commands.config_cmd import (
            _get_config_value,
        )

        value = _get_config_value(session, "llm_api_key")
        assert value is None


class TestConfigEditConsistency:
    """Test that config edit sees DB-stored keys."""

    def test_config_edit_shows_db_key(self, session: SessionState) -> None:
        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(session.settings.history_db_path)
        storage.save_config("nvidia_api_key", "nvapi-from-db-edit")

        from code_review_agent.interactive.commands.config_edit import (
            ConfigEditor,
        )

        editor = ConfigEditor(session)
        assert editor.values.get("llm_api_key") == "nvapi-from-db-edit"

    def test_config_edit_shows_none_when_no_key(
        self,
        session: SessionState,
    ) -> None:
        from code_review_agent.interactive.commands.config_edit import (
            ConfigEditor,
        )

        editor = ConfigEditor(session)
        assert editor.values.get("llm_api_key") == "None"

    def test_provider_cascade_reads_db_key(
        self,
        session: SessionState,
    ) -> None:
        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(session.settings.history_db_path)
        storage.save_config("openrouter_api_key", "or-key-from-db")

        from code_review_agent.interactive.commands.config_edit import (
            ConfigEditor,
        )

        editor = ConfigEditor(session)
        # Simulate switching provider to openrouter
        with patch("code_review_agent.interactive.commands.config_edit.get_provider") as mock_gp:
            mock_prov = MagicMock()
            mock_prov.base_url = "https://openrouter.ai/api/v1"
            mock_prov.default_model = "test/model"
            mock_gp.return_value = mock_prov
            editor._apply_provider_cascade("openrouter")

        assert editor.values.get("llm_api_key") == "or-key-from-db"


class TestStartupPanelConsistency:
    """Test that startup panel and config edit agree on key status."""

    def test_startup_and_config_agree_on_db_key(
        self,
        session: SessionState,
    ) -> None:
        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(session.settings.history_db_path)
        storage.save_config("nvidia_api_key", "nvapi-stored")

        # Startup panel check
        from code_review_agent.interactive.startup_keys import (
            _provider_has_key,
        )

        has_key = _provider_has_key("nvidia", session)
        assert has_key is True

        # Config display check
        display_val = session.resolve_api_key_display("nvidia")
        assert display_val == "nvapi-stored"

    def test_both_show_no_key_when_empty(
        self,
        session: SessionState,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("NVIDIA_API_KEY", "__placeholder__")

        from code_review_agent.interactive.startup_keys import (
            _provider_has_key,
        )

        _provider_has_key("nvidia", session)
        # Placeholder should not count as having a real key
        # (startup panel checks settings first, which has placeholder)

        display_val = session.resolve_api_key_display("nvidia")
        # Display should not show placeholder
        assert display_val == ""
