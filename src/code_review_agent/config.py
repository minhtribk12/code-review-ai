from __future__ import annotations

import os
from enum import StrEnum

from pydantic import Field, SecretStr, computed_field, model_validator
from pydantic_settings import BaseSettings

from code_review_agent.dedup import DedupStrategy
from code_review_agent.providers import get_base_url, get_default_model
from code_review_agent.token_budget import TokenTier

# Built-in providers with dedicated API key env var fields.
BUILTIN_PROVIDERS = frozenset({"nvidia", "openrouter"})


class LogLevel(StrEnum):
    """Supported log levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class Settings(BaseSettings):
    """Application configuration loaded from environment variables and .env file."""

    llm_provider: str = "nvidia"
    nvidia_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    llm_model: str = "nvidia/nemotron-3-super-120b-a12b"
    llm_base_url: str | None = None
    llm_temperature: float = Field(default=0.1, ge=0.0, le=1.0)
    llm_top_p: float = Field(default=0.95, ge=0.0, le=1.0)
    llm_max_tokens: int = Field(default=8192, ge=128)
    request_timeout_seconds: int = Field(default=120, ge=1)
    token_tier: TokenTier = TokenTier.FREE
    max_prompt_tokens: int | None = None
    max_tokens_per_review: int | None = None
    llm_input_price_per_m: float | None = None
    llm_output_price_per_m: float | None = None
    rate_limit_rpm: int | None = None
    dedup_strategy: DedupStrategy = DedupStrategy.EXACT
    max_review_seconds: int = Field(default=600, ge=10)
    max_pr_files: int = Field(default=200, ge=1)
    github_token: SecretStr | None = None
    github_rate_limit_warn_threshold: int = Field(default=100, ge=0)
    pr_stale_days: int = Field(default=7, ge=1)
    log_level: LogLevel = LogLevel.INFO
    max_concurrent_agents: int = Field(default=4, ge=1)
    interactive_history_file: str = "~/.cra_history"
    interactive_prompt: str = "cra> "
    interactive_vi_mode: bool = False
    interactive_autocomplete_cache_ttl: int = Field(default=5, ge=1)
    watch_debounce_seconds: float = Field(default=5.0, ge=1.0)
    default_agents: str = Field(
        default="",
        description=(
            "Comma-separated list of agents to run by default. "
            "Empty string means use tier defaults."
        ),
    )

    # Iterative deepening
    max_deepening_rounds: int = Field(default=1, ge=1, le=5)
    is_validation_enabled: bool = False
    max_validation_rounds: int = Field(default=1, ge=1, le=3)

    # Custom agents
    custom_agents_dir: str = "~/.cra/agents"

    # History
    history_db_path: str = "~/.cra/reviews.db"
    auto_save_history: bool = True

    # Usage display window for toolbar and progress bar
    # Options: session, hour, day, week, month, year, all
    usage_window: str = Field(default="hour")

    # Connection test: send a minimal request on startup and config changes
    test_connection_on_start: bool = True

    @model_validator(mode="after")
    def _validate_provider_in_registry(self) -> Settings:
        """Validate that the provider exists in the registry."""
        from code_review_agent.providers import PROVIDER_REGISTRY

        if self.llm_provider not in PROVIDER_REGISTRY:
            available = ", ".join(sorted(PROVIDER_REGISTRY.keys()))
            msg = (
                f"Unknown provider '{self.llm_provider}'. "
                f"Available: {available}. "
                f"Add custom providers with 'provider add' or edit ~/.cra/providers.json."
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_custom_pricing(self) -> Settings:
        """Validate that custom pricing is either both set or both unset."""
        has_input = self.llm_input_price_per_m is not None
        has_output = self.llm_output_price_per_m is not None
        if has_input != has_output:
            msg = (
                "LLM_INPUT_PRICE_PER_M and LLM_OUTPUT_PRICE_PER_M must both be set "
                "or both be unset. Set both for custom pricing, or leave both unset "
                "for auto-detection."
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_api_key_for_provider(self) -> Settings:
        """Validate that the active provider has an API key configured."""
        key = self._resolve_api_key()
        if key is None:
            env_name = f"{self.llm_provider.upper()}_API_KEY"
            msg = (
                f"{env_name} is required when llm_provider is '{self.llm_provider}'. "
                f"Set it in .env or as an environment variable."
            )
            raise ValueError(msg)
        return self

    def _resolve_api_key(self) -> SecretStr | None:
        """Resolve the API key for the current provider.

        Delegates to :meth:`resolve_api_key_for` with the active provider.
        """
        return self.resolve_api_key_for(self.llm_provider)

    def resolve_api_key_for(self, provider: str) -> SecretStr | None:
        """Resolve the API key for an arbitrary provider (not just the active one)."""
        if provider == "nvidia":
            return self.nvidia_api_key
        if provider == "openrouter":
            return self.openrouter_api_key
        env_key = f"{provider.upper()}_API_KEY"
        env_val = os.environ.get(env_key)
        if env_val:
            return SecretStr(env_val)
        return None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resolved_api_key(self) -> SecretStr:
        """Return the API key for the currently active provider."""
        key = self._resolve_api_key()
        if key is None:
            env_name = f"{self.llm_provider.upper()}_API_KEY"
            msg = f"No API key configured for provider '{self.llm_provider}' ({env_name})"
            raise ValueError(msg)
        return key

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resolved_llm_base_url(self) -> str:
        """Return the base URL for the configured LLM provider.

        If ``llm_base_url`` is set explicitly, use it (escape hatch for custom
        providers). Otherwise look up the provider in the registry.
        """
        if self.llm_base_url is not None:
            return self.llm_base_url
        return get_base_url(self.llm_provider)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resolved_default_model(self) -> str:
        """Return the default model for the currently active provider."""
        return get_default_model(self.llm_provider)

    model_config = {
        "env_file": ".env",
        "env_prefix": "",
        "extra": "ignore",
    }
