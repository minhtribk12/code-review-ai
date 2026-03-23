"""Tests for provider management, connection testing, config revert, and model display."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from code_review_agent.config import Settings
from code_review_agent.connection_test import FailureKind
from code_review_agent.connection_test import test_llm_connection as _do_test_connection
from code_review_agent.interactive.session import SessionState
from code_review_agent.providers import (
    ModelInfo,
    ProviderInfo,
    _merge_providers,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def real_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    """Real Settings with test API keys, isolated from .env."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-00000000")  # pragma: allowlist secret
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-00000000")  # pragma: allowlist secret
    monkeypatch.setenv("LLM_PROVIDER", "nvidia")
    monkeypatch.setenv("LLM_MODEL", "nvidia/nemotron-3-super-120b-a12b")
    return Settings()  # type: ignore[call-arg]


@pytest.fixture
def real_session(real_settings: Settings, tmp_path: Path) -> SessionState:
    """SessionState with real Settings for integration tests.

    Uses an isolated ConfigStore so tests never write to ``~/.cra/config.yaml``.
    """
    from code_review_agent.config_store import ConfigStore

    config_store = ConfigStore(path=tmp_path / "config.yaml")
    return SessionState(settings=real_settings, config_store=config_store)


@pytest.fixture
def user_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point user registry to a temp path across all relevant modules."""
    user_path = tmp_path / "providers.yaml"
    monkeypatch.setattr("code_review_agent.providers._USER_REGISTRY_PATH", user_path)
    monkeypatch.setattr(
        "code_review_agent.interactive.commands.provider_cmd._USER_REGISTRY_PATH",
        user_path,
    )
    return user_path


def _model(
    mid: str,
    name: str,
    *,
    is_free: bool = True,
    ctx: int = 128_000,
) -> dict[str, object]:
    """Build a model dict for test data."""
    return {"id": mid, "name": name, "is_free": is_free, "context_window": ctx}


def _write_user_providers(path: Path, providers: dict[str, Any]) -> None:
    """Write a providers.yaml file and reload the registry."""
    import yaml

    from code_review_agent.providers import reload_registry

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"providers": providers}, default_flow_style=False))
    reload_registry()


# ---------------------------------------------------------------------------
# Provider registry merge
# ---------------------------------------------------------------------------


class TestProviderRegistryMerge:
    """Test merging user-defined providers with bundled defaults."""

    def test_new_provider_added(self) -> None:
        base: dict[str, ProviderInfo] = {
            "nvidia": ProviderInfo(
                base_url="https://nvidia.example.com/v1",
                default_model="nvidia/test",
                rate_limit_rpm=40,
                models=(ModelInfo("nvidia/test", "Test", True, 128000),),
            ),
        }
        overlay: dict[str, Any] = {
            "ollama": {
                "base_url": "http://localhost:11434/v1",
                "default_model": "llama3.1",
                "rate_limit_rpm": 0,
                "models": [
                    _model("llama3.1", "Llama 3.1"),
                ],
            },
        }
        result = _merge_providers(base, overlay)
        assert "ollama" in result
        assert "nvidia" in result
        assert result["ollama"].base_url == "http://localhost:11434/v1"

    def test_existing_provider_extended_with_new_models(self) -> None:
        base: dict[str, ProviderInfo] = {
            "nvidia": ProviderInfo(
                base_url="https://nvidia.example.com/v1",
                default_model="nvidia/model-a",
                rate_limit_rpm=40,
                models=(ModelInfo("nvidia/model-a", "Model A", True, 128000),),
            ),
        }
        overlay: dict[str, Any] = {
            "nvidia": {
                "models": [
                    _model("nvidia/model-b", "Model B", is_free=False, ctx=64000),
                ],
            },
        }
        result = _merge_providers(base, overlay)
        assert len(result["nvidia"].models) == 2
        ids = {m.id for m in result["nvidia"].models}
        assert ids == {"nvidia/model-a", "nvidia/model-b"}

    def test_duplicate_model_not_added_twice(self) -> None:
        base: dict[str, ProviderInfo] = {
            "nvidia": ProviderInfo(
                base_url="https://nvidia.example.com/v1",
                default_model="nvidia/model-a",
                rate_limit_rpm=40,
                models=(ModelInfo("nvidia/model-a", "Model A", True, 128000),),
            ),
        }
        overlay: dict[str, Any] = {
            "nvidia": {
                "models": [
                    _model("nvidia/model-a", "Copy"),
                ],
            },
        }
        result = _merge_providers(base, overlay)
        assert len(result["nvidia"].models) == 1

    def test_same_model_name_different_providers(self) -> None:
        """Different providers can share the same model ID."""
        base: dict[str, ProviderInfo] = {
            "prov-a": ProviderInfo(
                base_url="https://a.example.com/v1",
                default_model="shared/model",
                rate_limit_rpm=10,
                models=(ModelInfo("shared/model", "Shared", True, 128000),),
            ),
        }
        overlay: dict[str, Any] = {
            "prov-b": {
                "base_url": "https://b.example.com/v1",
                "default_model": "shared/model",
                "rate_limit_rpm": 20,
                "models": [
                    _model("shared/model", "Shared"),
                ],
            },
        }
        result = _merge_providers(base, overlay)
        assert "shared/model" in result["prov-a"].model_ids()
        assert "shared/model" in result["prov-b"].model_ids()


# ---------------------------------------------------------------------------
# User registry persistence
# ---------------------------------------------------------------------------


class TestUserRegistryPersistence:
    """Test saving/loading user providers to disk."""

    def test_save_and_reload(self, user_registry: Path) -> None:
        _write_user_providers(
            user_registry,
            {
                "custom": {
                    "base_url": "http://localhost:8000/v1",
                    "default_model": "custom/model",
                    "rate_limit_rpm": 5,
                    "models": [
                        _model("custom/model", "Custom", ctx=32000),
                    ],
                },
            },
        )

        from code_review_agent.providers import PROVIDER_REGISTRY

        assert "custom" in PROVIDER_REGISTRY
        assert PROVIDER_REGISTRY["custom"].base_url == "http://localhost:8000/v1"

    def test_empty_user_registry(self, user_registry: Path) -> None:
        from code_review_agent.interactive.commands.provider_cmd import _load_user_registry

        result = _load_user_registry()
        assert result == {}


# ---------------------------------------------------------------------------
# Connection test failure categorization
# ---------------------------------------------------------------------------


class TestConnectionTestFailureKind:
    """Test that connection failures are categorized correctly."""

    def test_success_returns_none_kind(self) -> None:
        mock_settings = MagicMock()
        mock_settings.resolved_api_key.get_secret_value.return_value = "test-key"
        mock_settings.resolved_llm_base_url = "https://example.com/v1"
        mock_settings.llm_model = "test/model"

        with patch("code_review_agent.connection_test.openai.OpenAI") as mock_client:
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.model = "test/model"
            mock_client.return_value.chat.completions.create.return_value = mock_response

            is_ok, _, kind = _do_test_connection(mock_settings)

        assert is_ok is True
        assert kind == FailureKind.NONE

    def test_not_found_returns_model_kind(self) -> None:
        import openai

        mock_settings = MagicMock()
        mock_settings.resolved_api_key.get_secret_value.return_value = "test-key"
        mock_settings.resolved_llm_base_url = "https://example.com/v1"
        mock_settings.llm_model = "bad/model"

        with patch("code_review_agent.connection_test.openai.OpenAI") as mock_client:
            mock_client.return_value.chat.completions.create.side_effect = openai.NotFoundError(
                message="Model not found",
                response=MagicMock(status_code=404),
                body=None,
            )
            is_ok, _, kind = _do_test_connection(mock_settings)

        assert is_ok is False
        assert kind == FailureKind.MODEL

    def test_auth_error_returns_provider_kind(self) -> None:
        import openai

        mock_settings = MagicMock()
        mock_settings.resolved_api_key.get_secret_value.return_value = "bad-key"
        mock_settings.resolved_llm_base_url = "https://example.com/v1"
        mock_settings.llm_model = "test/model"

        with patch("code_review_agent.connection_test.openai.OpenAI") as mock_client:
            mock_client.return_value.chat.completions.create.side_effect = (
                openai.AuthenticationError(
                    message="Invalid key",
                    response=MagicMock(status_code=401),
                    body=None,
                )
            )
            is_ok, _, kind = _do_test_connection(mock_settings)

        assert is_ok is False
        assert kind == FailureKind.PROVIDER

    def test_connection_error_returns_provider_kind(self) -> None:
        import openai

        mock_settings = MagicMock()
        mock_settings.resolved_api_key.get_secret_value.return_value = "test-key"
        mock_settings.resolved_llm_base_url = "https://unreachable.example.com/v1"
        mock_settings.llm_model = "test/model"

        with patch("code_review_agent.connection_test.openai.OpenAI") as mock_client:
            mock_client.return_value.chat.completions.create.side_effect = (
                openai.APIConnectionError(request=MagicMock())
            )
            is_ok, _, kind = _do_test_connection(mock_settings)

        assert is_ok is False
        assert kind == FailureKind.PROVIDER

    def test_rate_limit_treated_as_success(self) -> None:
        import openai

        mock_settings = MagicMock()
        mock_settings.resolved_api_key.get_secret_value.return_value = "test-key"
        mock_settings.resolved_llm_base_url = "https://example.com/v1"
        mock_settings.llm_model = "test/model"

        with patch("code_review_agent.connection_test.openai.OpenAI") as mock_client:
            mock_client.return_value.chat.completions.create.side_effect = openai.RateLimitError(
                message="Rate limited",
                response=MagicMock(status_code=429),
                body=None,
            )
            is_ok, _, kind = _do_test_connection(mock_settings)

        assert is_ok is True
        assert kind == FailureKind.NONE


# ---------------------------------------------------------------------------
# Config revert on failed connection test
# ---------------------------------------------------------------------------


class TestConnectionTestRevert:
    """Test that failed connection tests revert LLM config to previous values."""

    def test_revert_on_model_failure(self, real_session: SessionState) -> None:
        """Changing to a bad model should revert to previous model."""
        prev_config = {
            "llm_provider": "nvidia",
            "llm_model": "nvidia/nemotron-3-super-120b-a12b",
            "llm_base_url": "https://integrate.api.nvidia.com/v1",
        }

        real_session.config_overrides["llm_model"] = "nvidia/nonexistent-model"
        real_session.invalidate_settings_cache()

        with (
            patch(
                "code_review_agent.connection_test.test_llm_connection",
                return_value=(False, "Model not found", FailureKind.MODEL),
            ),
            patch("code_review_agent.interactive.repl.console"),
        ):
            from code_review_agent.interactive.repl import run_connection_test

            result = run_connection_test(
                real_session,
                previous_llm_config=prev_config,
            )

        assert result is False
        assert real_session.config_overrides["llm_model"] == "nvidia/nemotron-3-super-120b-a12b"

    def test_revert_on_provider_failure(self, real_session: SessionState) -> None:
        """Changing to a bad provider should revert to previous provider."""
        prev_config = {
            "llm_provider": "nvidia",
            "llm_model": "nvidia/nemotron-3-super-120b-a12b",
            "llm_base_url": "https://integrate.api.nvidia.com/v1",
        }

        real_session.config_overrides["llm_provider"] = "openrouter"
        real_session.config_overrides["llm_model"] = "nvidia/nemotron-3-super-120b-a12b:free"
        real_session.config_overrides["llm_base_url"] = "https://openrouter.ai/api/v1"
        real_session.invalidate_settings_cache()

        with (
            patch(
                "code_review_agent.connection_test.test_llm_connection",
                return_value=(False, "Auth failed", FailureKind.PROVIDER),
            ),
            patch("code_review_agent.interactive.repl.console"),
        ):
            from code_review_agent.interactive.repl import run_connection_test

            result = run_connection_test(
                real_session,
                previous_llm_config=prev_config,
            )

        assert result is False
        assert real_session.config_overrides["llm_provider"] == "nvidia"
        assert real_session.config_overrides["llm_model"] == "nvidia/nemotron-3-super-120b-a12b"

    def test_no_revert_without_previous_config(self, real_session: SessionState) -> None:
        """Without previous config, broken values stay (offers removal instead)."""
        real_session.config_overrides["llm_model"] = "bad/model"
        real_session.invalidate_settings_cache()

        with (
            patch(
                "code_review_agent.connection_test.test_llm_connection",
                return_value=(False, "Model not found", FailureKind.MODEL),
            ),
            patch("code_review_agent.interactive.repl.console"),
            patch("code_review_agent.interactive.repl._offer_model_removal"),
        ):
            from code_review_agent.interactive.repl import run_connection_test

            run_connection_test(real_session)

        assert real_session.config_overrides["llm_model"] == "bad/model"

    def test_successful_connection_clears_health_marks(
        self,
        real_session: SessionState,
    ) -> None:
        with (
            patch(
                "code_review_agent.connection_test.test_llm_connection",
                return_value=(True, "Connected", FailureKind.NONE),
            ),
            patch("code_review_agent.interactive.repl.console"),
            patch("code_review_agent.interactive.repl._set_health_mark") as mock_set,
        ):
            from code_review_agent.interactive.repl import run_connection_test

            result = run_connection_test(real_session)

        assert result is True
        assert mock_set.call_count == 2


# ---------------------------------------------------------------------------
# Health status marks
# ---------------------------------------------------------------------------


class TestHealthStatusDisplay:
    """Test that health marks are stored and retrieved correctly."""

    def test_mark_and_retrieve(self, real_session: SessionState) -> None:
        from code_review_agent.interactive.repl import (
            _set_health_mark,
            get_health_status,
        )

        _set_health_mark(real_session, "model", "bad/model", is_healthy=False)
        _set_health_mark(real_session, "provider", "bad-provider", is_healthy=False)

        health = get_health_status(real_session)
        assert "bad/model" in health["model"]
        assert "bad-provider" in health["provider"]

        _set_health_mark(real_session, "model", "bad/model", is_healthy=True)
        health = get_health_status(real_session)
        assert "bad/model" not in health["model"]
        assert "bad-provider" in health["provider"]


# ---------------------------------------------------------------------------
# Model display in config edit selectors
# ---------------------------------------------------------------------------


class TestModelDisplayInConfigEdit:
    """Test that model names show correctly in config edit selectors."""

    def test_model_choices_from_nvidia(self, real_session: SessionState) -> None:
        from code_review_agent.interactive.commands.config_edit import _get_model_choices

        choices = _get_model_choices(
            {"llm_provider": "nvidia"},
            real_session.settings,
        )
        assert "nvidia/nemotron-3-super-120b-a12b" in choices
        assert "nvidia/nemotron-3-nano-30b-a3b" in choices

    def test_model_choices_from_openrouter(self, real_session: SessionState) -> None:
        from code_review_agent.interactive.commands.config_edit import _get_model_choices

        choices = _get_model_choices(
            {"llm_provider": "openrouter"},
            real_session.settings,
        )
        assert "nvidia/nemotron-3-super-120b-a12b:free" in choices

    def test_model_choices_from_custom_provider(
        self,
        real_session: SessionState,
        user_registry: Path,
    ) -> None:
        """Models from a user-added provider should appear in the selector."""
        from code_review_agent.interactive.commands.config_edit import _get_model_choices

        _write_user_providers(
            user_registry,
            {
                "custom": {
                    "base_url": "http://localhost:8000/v1",
                    "default_model": "custom/my-model",
                    "rate_limit_rpm": 10,
                    "models": [
                        _model("custom/my-model", "My Model", ctx=32000),
                        _model("custom/other", "Other Model", is_free=False, ctx=64000),
                    ],
                },
            },
        )

        choices = _get_model_choices(
            {"llm_provider": "custom"},
            real_session.settings,
        )
        assert "custom/my-model" in choices
        assert "custom/other" in choices

    def test_provider_choices_include_custom(
        self,
        real_session: SessionState,
        user_registry: Path,
    ) -> None:
        """Custom providers should appear in the provider selector."""
        _write_user_providers(
            user_registry,
            {
                "my-server": {
                    "base_url": "http://my-server:8000/v1",
                    "default_model": "my-server/model",
                    "rate_limit_rpm": 10,
                    "models": [
                        _model("my-server/model", "Model", ctx=32000),
                    ],
                },
            },
        )

        from code_review_agent.providers import PROVIDER_REGISTRY

        assert "my-server" in PROVIDER_REGISTRY

    def test_model_annotations_show_name_and_context(
        self,
        real_session: SessionState,
    ) -> None:
        """Model selector should show display name, free status, and context."""
        from code_review_agent.interactive.commands.config_edit import ConfigEditor

        real_session.config_overrides["llm_provider"] = "nvidia"
        editor = ConfigEditor(real_session)

        for i, (name, kind) in enumerate(editor.rows):
            if name == "llm_model" and kind == "field":
                editor.cursor = i
                break
        editor.start_edit()

        assert editor.mode == "select"
        assert editor.selector_key == "llm_model"

        rendered = editor.render()
        text = "".join(t[1] for t in rendered)
        assert "Nemotron 3 Super 120B" in text
        assert "free" in text
        assert "ctx" in text


# ---------------------------------------------------------------------------
# Provider cascade on change
# ---------------------------------------------------------------------------


class TestProviderCascade:
    """Test that changing provider updates model and base_url."""

    def test_cascade_in_config_editor(self, real_session: SessionState) -> None:
        from code_review_agent.interactive.commands.config_edit import ConfigEditor

        editor = ConfigEditor(real_session)
        editor._apply_provider_cascade("openrouter")

        assert editor.values["llm_model"] == "nvidia/nemotron-3-super-120b-a12b:free"
        assert editor.values["llm_base_url"] == "https://openrouter.ai/api/v1"
        assert "llm_model" in editor.changed_keys
        assert "llm_base_url" in editor.changed_keys

    def test_cascade_custom_provider(
        self,
        real_session: SessionState,
        user_registry: Path,
    ) -> None:
        from code_review_agent.interactive.commands.config_edit import ConfigEditor

        _write_user_providers(
            user_registry,
            {
                "ollama": {
                    "base_url": "http://localhost:11434/v1",
                    "default_model": "llama3.1",
                    "rate_limit_rpm": 0,
                    "models": [
                        _model("llama3.1", "Llama 3.1"),
                    ],
                },
            },
        )

        editor = ConfigEditor(real_session)
        editor._apply_provider_cascade("ollama")

        assert editor.values["llm_model"] == "llama3.1"
        assert editor.values["llm_base_url"] == "http://localhost:11434/v1"


# ---------------------------------------------------------------------------
# Config edit change detection
# ---------------------------------------------------------------------------


class TestConfigEditChangeDetection:
    """Test that only actual changes are tracked."""

    def test_same_value_not_marked_as_changed(self, real_session: SessionState) -> None:
        from code_review_agent.interactive.commands.config_edit import ConfigEditor

        editor = ConfigEditor(real_session)
        original_temp = editor.values.get("llm_temperature", "0.1")

        editor._apply_value("llm_temperature", original_temp)

        assert "llm_temperature" not in editor.changed_keys
        assert not editor.has_changes

    def test_different_value_marked_as_changed(self, real_session: SessionState) -> None:
        from code_review_agent.interactive.commands.config_edit import ConfigEditor

        editor = ConfigEditor(real_session)
        editor._apply_value("llm_temperature", "0.9")

        assert "llm_temperature" in editor.changed_keys
        assert editor.has_changes

    def test_revert_to_original_removes_change(self, real_session: SessionState) -> None:
        from code_review_agent.interactive.commands.config_edit import ConfigEditor

        editor = ConfigEditor(real_session)
        original = editor.values.get("llm_temperature", "0.1")

        editor._apply_value("llm_temperature", "0.9")
        assert "llm_temperature" in editor.changed_keys

        editor._apply_value("llm_temperature", original)
        assert "llm_temperature" not in editor.changed_keys
        assert "llm_temperature" not in real_session.config_overrides


# ---------------------------------------------------------------------------
# Provider validation
# ---------------------------------------------------------------------------


class TestProviderValidation:
    """Test provider validation in Settings."""

    def test_unknown_provider_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")  # pragma: allowlist secret
        monkeypatch.setenv("LLM_PROVIDER", "nonexistent_provider")

        with pytest.raises(Exception, match="Unknown provider"):
            Settings()  # type: ignore[call-arg]

    def test_nvidia_provider_accepted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")  # pragma: allowlist secret
        monkeypatch.setenv("LLM_PROVIDER", "nvidia")

        settings = Settings()  # type: ignore[call-arg]
        assert settings.llm_provider == "nvidia"

    def test_missing_api_key_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("LLM_PROVIDER", "nvidia")
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        with pytest.raises(Exception, match="NVIDIA_API_KEY"):
            Settings()  # type: ignore[call-arg]

    def test_custom_provider_with_env_api_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        user_registry: Path,
    ) -> None:
        """Custom providers read API key from {PROVIDER}_API_KEY env var."""
        _write_user_providers(
            user_registry,
            {
                "myhost": {
                    "base_url": "http://localhost:8000/v1",
                    "default_model": "myhost/model",
                    "rate_limit_rpm": 10,
                    "models": [
                        _model("myhost/model", "Model", ctx=32000),
                    ],
                },
            },
        )

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("LLM_PROVIDER", "myhost")
        monkeypatch.setenv("MYHOST_API_KEY", "myhost-key-123")  # pragma: allowlist secret
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")  # pragma: allowlist secret

        settings = Settings()  # type: ignore[call-arg]
        assert settings.llm_provider == "myhost"
        assert settings.resolved_api_key.get_secret_value() == "myhost-key-123"


# ---------------------------------------------------------------------------
# save_config_to_yaml filtering
# ---------------------------------------------------------------------------


class TestSaveConfigFiltering:
    """Test that save_config_to_yaml skips API keys."""

    def test_skips_api_keys(
        self,
        real_session: SessionState,
        tmp_path: Path,
    ) -> None:
        from code_review_agent.config_store import ConfigStore
        from code_review_agent.interactive.commands.config_cmd import save_config_to_yaml

        real_session.config_store = ConfigStore(path=tmp_path / "config.yaml")

        real_session.config_overrides["custom_api_key"] = "secret"  # pragma: allowlist secret
        real_session.config_overrides["llm_temperature"] = "0.5"
        saved = save_config_to_yaml(real_session)
        # llm_temperature saved; api_key skipped
        assert saved == 1


# ---------------------------------------------------------------------------
# Config categories completeness
# ---------------------------------------------------------------------------


class TestConfigCategories:
    """Test config command category completeness."""

    def test_config_cmd_has_interactive_category(self) -> None:
        from code_review_agent.interactive.commands.config_cmd import _CONFIG_CATEGORIES

        assert "Interactive" in _CONFIG_CATEGORIES
        assert "interactive_vi_mode" in _CONFIG_CATEGORIES["Interactive"]

    def test_config_cmd_has_logging_category(self) -> None:
        from code_review_agent.interactive.commands.config_cmd import _CONFIG_CATEGORIES

        assert "Logging" in _CONFIG_CATEGORIES
        assert "log_level" in _CONFIG_CATEGORIES["Logging"]


# ---------------------------------------------------------------------------
# Virtual llm_api_key field in config editor
# ---------------------------------------------------------------------------


class TestVirtualApiKeyField:
    """Test that llm_api_key maps to the current provider's key."""

    def test_reads_nvidia_key(self, real_session: SessionState) -> None:
        from code_review_agent.interactive.commands.config_edit import ConfigEditor

        editor = ConfigEditor(real_session)
        # Should read nvidia_api_key since provider is nvidia
        assert editor.values.get("llm_api_key") is not None
        assert editor.values["llm_api_key"] != "None"  # pragma: allowlist secret

    def test_updates_on_provider_cascade(self, real_session: SessionState) -> None:
        from code_review_agent.interactive.commands.config_edit import ConfigEditor

        editor = ConfigEditor(real_session)

        # Cascade to openrouter
        editor._apply_provider_cascade("openrouter")

        # Key should now reflect openrouter's key
        new_key = editor.values.get("llm_api_key", "")
        # Both keys are set in real_settings, so both should be non-empty
        assert new_key != "None"


# ---------------------------------------------------------------------------
# Health mark consolidation via _set_health_mark
# ---------------------------------------------------------------------------


class TestHealthMarkConsolidation:
    """Test the consolidated _set_health_mark function."""

    def test_mark_then_clear(
        self,
        real_session: SessionState,
        tmp_path: Path,
    ) -> None:
        from code_review_agent.config_store import ConfigStore
        from code_review_agent.interactive.repl import (
            _set_health_mark,
            get_health_status,
        )

        real_session.config_store = ConfigStore(path=tmp_path / "config.yaml")

        _set_health_mark(real_session, "provider", "test-prov", is_healthy=False)
        health = get_health_status(real_session)
        assert "test-prov" in health["provider"]

        _set_health_mark(real_session, "provider", "test-prov", is_healthy=True)
        health = get_health_status(real_session)
        assert "test-prov" not in health["provider"]


# ---------------------------------------------------------------------------
# API key resolution
# ---------------------------------------------------------------------------


class TestApiKeyResolution:
    """Test API key resolution logic."""

    def test_resolve_delegates(self, real_settings: Settings) -> None:
        direct = real_settings.resolve_api_key_for("nvidia")
        resolved = real_settings.resolved_api_key
        assert direct is not None
        assert resolved.get_secret_value() == direct.get_secret_value()


# ---------------------------------------------------------------------------
# Startup key setup
# ---------------------------------------------------------------------------


class TestStartupKeySetup:
    """Test the startup key checking and local provider detection."""

    def test_is_local_provider_localhost(self, user_registry: Path) -> None:
        from code_review_agent.interactive.startup_keys import _is_local_provider

        _write_user_providers(
            user_registry,
            {
                "local": {
                    "base_url": "http://localhost:11434/v1",
                    "default_model": "llama3.1",
                    "rate_limit_rpm": 0,
                    "models": [_model("llama3.1", "Llama 3.1")],
                },
            },
        )
        assert _is_local_provider("local") is True

    def test_is_local_provider_private_ip(self, user_registry: Path) -> None:
        from code_review_agent.interactive.startup_keys import _is_local_provider

        _write_user_providers(
            user_registry,
            {
                "gpu": {
                    "base_url": "http://192.168.1.100:8000/v1",
                    "default_model": "model",
                    "rate_limit_rpm": 0,
                    "models": [_model("model", "Model")],
                },
            },
        )
        assert _is_local_provider("gpu") is True

    def test_is_local_provider_public_url(self) -> None:
        from code_review_agent.interactive.startup_keys import _is_local_provider

        assert _is_local_provider("nvidia") is False
        assert _is_local_provider("openrouter") is False

    def test_is_local_provider_unknown(self) -> None:
        from code_review_agent.interactive.startup_keys import _is_local_provider

        assert _is_local_provider("nonexistent") is False

    def test_check_providers_ready_with_key(
        self,
        real_session: SessionState,
    ) -> None:
        from code_review_agent.interactive.startup_keys import check_providers_ready

        # real_session has nvidia key set
        assert check_providers_ready(real_session) is True

    def test_check_providers_ready_with_local(
        self,
        real_session: SessionState,
        user_registry: Path,
    ) -> None:
        from code_review_agent.interactive.startup_keys import check_providers_ready

        _write_user_providers(
            user_registry,
            {
                "local": {
                    "base_url": "http://localhost:11434/v1",
                    "default_model": "llama3.1",
                    "rate_limit_rpm": 0,
                    "models": [_model("llama3.1", "Llama 3.1")],
                },
            },
        )
        assert check_providers_ready(real_session) is True

    def test_provider_has_key_from_env(
        self,
        real_session: SessionState,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from code_review_agent.interactive.startup_keys import _provider_has_key

        monkeypatch.setenv("CUSTOM_API_KEY", "test-key")  # pragma: allowlist secret
        assert _provider_has_key("nvidia", real_session) is True

    def test_provider_has_key_local_no_key_needed(
        self,
        real_session: SessionState,
        user_registry: Path,
    ) -> None:
        from code_review_agent.interactive.startup_keys import _provider_has_key

        _write_user_providers(
            user_registry,
            {
                "ollama": {
                    "base_url": "http://127.0.0.1:11434/v1",
                    "default_model": "llama3.1",
                    "rate_limit_rpm": 0,
                    "models": [_model("llama3.1", "Llama 3.1")],
                },
            },
        )
        assert _provider_has_key("ollama", real_session) is True
