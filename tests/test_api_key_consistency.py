"""Tests for API key consistency across DB and .env storage layers.

DB is the primary source, .env is the fallback. All interactive write
paths save to DB. The sync panel allows bidirectional sync.
"""  # pragma: allowlist secret

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from code_review_agent.interactive.session import SessionState

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def tmp_db(tmp_path: Path) -> str:
    return str(tmp_path / "test_reviews.db")


@pytest.fixture
def session(tmp_db: str, monkeypatch: pytest.MonkeyPatch) -> SessionState:
    monkeypatch.setenv("NVIDIA_API_KEY", "__placeholder__")  # pragma: allowlist secret
    monkeypatch.setenv("LLM_PROVIDER", "nvidia")

    from code_review_agent.config import Settings

    settings = MagicMock(spec=Settings)
    settings.history_db_path = tmp_db
    settings.llm_provider = "nvidia"
    settings.llm_model = "nvidia/test-model"
    settings.nvidia_api_key = None
    settings.openrouter_api_key = None
    settings.model_fields = {}

    return SessionState(settings=settings)


# ---------------------------------------------------------------------------
# resolve_api_key_display: DB-first, .env fallback
# ---------------------------------------------------------------------------


class TestResolveApiKeyDisplay:
    def test_returns_empty_when_no_key(self, session: SessionState) -> None:
        assert session.resolve_api_key_display("nvidia") == ""

    def test_db_has_highest_priority(self, session: SessionState) -> None:
        from pydantic import SecretStr

        from code_review_agent.storage import ReviewStorage

        # Set key in both DB and .env
        storage = ReviewStorage(session.settings.history_db_path)
        storage.save_config(  # pragma: allowlist secret
            "nvidia_api_key",
            "nvapi-from-db",
        )
        session.settings.nvidia_api_key = SecretStr("nvapi-from-env")

        result = session.resolve_api_key_display("nvidia")
        assert result == "nvapi-from-db"

    def test_falls_back_to_env_when_db_empty(
        self,
        session: SessionState,
    ) -> None:
        from pydantic import SecretStr

        session.settings.nvidia_api_key = SecretStr("nvapi-from-env")
        result = session.resolve_api_key_display("nvidia")
        assert result == "nvapi-from-env"

    def test_reads_from_db(self, session: SessionState) -> None:
        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(session.settings.history_db_path)
        storage.save_config(  # pragma: allowlist secret
            "nvidia_api_key",
            "nvapi-db-only",
        )

        assert session.resolve_api_key_display("nvidia") == "nvapi-db-only"

    def test_ignores_placeholder(self, session: SessionState) -> None:
        from pydantic import SecretStr

        session.settings.nvidia_api_key = SecretStr("__placeholder__")
        assert session.resolve_api_key_display("nvidia") == ""

    def test_custom_provider_from_db(self, session: SessionState) -> None:
        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(session.settings.history_db_path)
        storage.save_config(  # pragma: allowlist secret
            "ollama_api_key",
            "ollama-key",
        )

        assert session.resolve_api_key_display("ollama") == "ollama-key"

    def test_uses_active_provider(self, session: SessionState) -> None:
        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(session.settings.history_db_path)
        storage.save_config(  # pragma: allowlist secret
            "nvidia_api_key",
            "nvapi-active",
        )

        assert session.resolve_api_key_display() == "nvapi-active"


# ---------------------------------------------------------------------------
# save / delete / inject
# ---------------------------------------------------------------------------


class TestSaveAndDelete:
    def test_save_to_db_and_env(
        self,
        session: SessionState,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        session.save_api_key_to_db("nvidia", "nvapi-saved")
        assert session.load_api_key_from_db("nvidia") == "nvapi-saved"
        assert (
            os.environ.get(  # pragma: allowlist secret
                "NVIDIA_API_KEY",
            )
            == "nvapi-saved"
        )

    def test_delete_from_db_and_env(
        self,
        session: SessionState,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        session.save_api_key_to_db("nvidia", "nvapi-to-delete")
        session.delete_api_key_from_db("nvidia")

        assert session.load_api_key_from_db("nvidia") == ""
        assert os.environ.get("NVIDIA_API_KEY") is None  # pragma: allowlist secret

    def test_inject_db_keys_overwrites_env(
        self,
        session: SessionState,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("NVIDIA_API_KEY", "old-env-val")
        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(session.settings.history_db_path)
        storage.save_config(  # pragma: allowlist secret
            "nvidia_api_key",
            "db-val",
        )

        session._inject_db_api_keys_to_env()
        assert (
            os.environ.get(  # pragma: allowlist secret
                "NVIDIA_API_KEY",
            )
            == "db-val"
        )


# ---------------------------------------------------------------------------
# config show / config edit consistency
# ---------------------------------------------------------------------------


class TestConfigCmdConsistency:
    def test_config_get_sees_db_key(self, session: SessionState) -> None:
        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(session.settings.history_db_path)
        storage.save_config(  # pragma: allowlist secret
            "nvidia_api_key",
            "nvapi-visible",
        )

        from code_review_agent.interactive.commands.config_cmd import (
            _get_config_value,
        )

        value = _get_config_value(session, "llm_api_key")
        assert value is not None

        from pydantic import SecretStr

        if isinstance(value, SecretStr):
            assert value.get_secret_value() == "nvapi-visible"

    def test_config_get_returns_none_when_no_key(
        self,
        session: SessionState,
    ) -> None:
        from code_review_agent.interactive.commands.config_cmd import (
            _get_config_value,
        )

        assert _get_config_value(session, "llm_api_key") is None


class TestConfigEditConsistency:
    def test_config_edit_shows_db_key(self, session: SessionState) -> None:
        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(session.settings.history_db_path)
        storage.save_config(  # pragma: allowlist secret
            "nvidia_api_key",
            "nvapi-edit-vis",
        )

        from code_review_agent.interactive.commands.config_edit import (
            ConfigEditor,
        )

        editor = ConfigEditor(session)
        assert editor.values.get("llm_api_key") == "nvapi-edit-vis"

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
        storage.save_config(  # pragma: allowlist secret
            "openrouter_api_key",
            "or-key-db",
        )

        from code_review_agent.interactive.commands.config_edit import (
            ConfigEditor,
        )

        editor = ConfigEditor(session)
        with patch("code_review_agent.interactive.commands.config_edit.get_provider") as mock_gp:
            mock_prov = MagicMock()
            mock_prov.base_url = "https://openrouter.ai/api/v1"
            mock_prov.default_model = "test/model"
            mock_gp.return_value = mock_prov
            editor._apply_provider_cascade("openrouter")

        assert editor.values.get("llm_api_key") == "or-key-db"


# ---------------------------------------------------------------------------
# Keys panel
# ---------------------------------------------------------------------------


class TestKeysPanel:
    def test_panel_init(self, session: SessionState) -> None:
        from code_review_agent.interactive.commands.keys_panel import (
            _KeysPanel,
        )

        panel = _KeysPanel(session)
        assert len(panel.providers) > 0
        assert panel.mode == "navigate"

    def test_panel_shows_db_key(self, session: SessionState) -> None:
        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(session.settings.history_db_path)
        storage.save_config(  # pragma: allowlist secret
            "nvidia_api_key",
            "nvapi-panel",
        )

        from code_review_agent.interactive.commands.keys_panel import (
            _KeysPanel,
        )

        panel = _KeysPanel(session)
        assert panel._db_key("nvidia") == "nvapi-panel"

    def test_sync_env_to_db(self, session: SessionState) -> None:
        from pydantic import SecretStr

        session.settings.nvidia_api_key = SecretStr("nvapi-env-sync")

        from code_review_agent.interactive.commands.keys_panel import (
            _KeysPanel,
            _Mode,
        )

        panel = _KeysPanel(session)
        for i, p in enumerate(panel.providers):
            if p == "nvidia":
                panel.cursor = i
                break

        panel.start_sync()
        assert panel.mode == _Mode.SYNC_POPUP

        panel.sync_cursor = 1  # .env -> DB
        panel.confirm_sync()

        assert session.load_api_key_from_db("nvidia") == "nvapi-env-sync"
        assert panel.mode == _Mode.NAVIGATE

    def test_delete_key(self, session: SessionState) -> None:
        session.save_api_key_to_db("nvidia", "nvapi-to-del")

        from code_review_agent.interactive.commands.keys_panel import (
            _KeysPanel,
            _Mode,
        )

        panel = _KeysPanel(session)
        for i, p in enumerate(panel.providers):
            if p == "nvidia":
                panel.cursor = i
                break

        panel.start_delete()
        assert panel.mode == _Mode.DELETE_CONFIRM
        panel.confirm_delete()

        assert session.load_api_key_from_db("nvidia") == ""
        assert panel.mode == _Mode.NAVIGATE

    def test_render_produces_output(self, session: SessionState) -> None:
        from prompt_toolkit.formatted_text import FormattedText as FT

        from code_review_agent.interactive.commands.keys_panel import (
            _KeysPanel,
        )

        panel = _KeysPanel(session)
        result = panel.render()
        assert isinstance(result, FT)
        text = "".join(t[1] for t in result)
        assert "API Key Manager" in text
        assert "Provider" in text


class TestWriteEnvKey:
    def test_writes_new_key(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("LLM_PROVIDER=nvidia\n")

        from code_review_agent.interactive.commands.keys_panel import (
            _write_env_key,
        )

        with patch(
            "code_review_agent.interactive.commands.keys_panel._find_env_file",
            return_value=env_file,
        ):
            result = _write_env_key("nvidia", "nvapi-new")

        assert result is True
        content = env_file.read_text()
        assert "NVIDIA_API_KEY=nvapi-new" in content
        assert "LLM_PROVIDER=nvidia" in content

    def test_updates_existing_key(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "LLM_PROVIDER=nvidia\nNVIDIA_API_KEY=old-value\n"  # pragma: allowlist secret
        )

        from code_review_agent.interactive.commands.keys_panel import (
            _write_env_key,
        )

        with patch(
            "code_review_agent.interactive.commands.keys_panel._find_env_file",
            return_value=env_file,
        ):
            result = _write_env_key("nvidia", "nvapi-updated")

        assert result is True
        content = env_file.read_text()
        assert "NVIDIA_API_KEY=nvapi-updated" in content
        assert "old-value" not in content
