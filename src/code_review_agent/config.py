from __future__ import annotations

from enum import StrEnum

from pydantic import Field, SecretStr, computed_field, model_validator
from pydantic_settings import BaseSettings

from code_review_agent.dedup import DedupStrategy
from code_review_agent.token_budget import TokenTier


class KnownProvider(StrEnum):
    """Supported LLM API providers."""

    OPENROUTER = "openrouter"
    NVIDIA = "nvidia"
    OPENAI = "openai"


class LogLevel(StrEnum):
    """Supported log levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


_PROVIDER_BASE_URLS: dict[KnownProvider, str] = {
    KnownProvider.OPENROUTER: "https://openrouter.ai/api/v1",
    KnownProvider.NVIDIA: "https://integrate.api.nvidia.com/v1",
    KnownProvider.OPENAI: "https://api.openai.com/v1",
}


class Settings(BaseSettings):
    """Application configuration loaded from environment variables and .env file."""

    llm_provider: KnownProvider = KnownProvider.OPENROUTER
    llm_api_key: SecretStr
    llm_model: str = "nvidia/nemotron-3-super-120b-a12b"
    llm_base_url: str | None = None
    llm_temperature: float = Field(default=0.1, ge=0.0, le=1.0)
    request_timeout_seconds: int = Field(default=120, ge=1)
    token_tier: TokenTier = TokenTier.FREE
    max_prompt_tokens: int | None = None
    max_tokens_per_review: int | None = None
    llm_input_price_per_m: float | None = None
    llm_output_price_per_m: float | None = None
    rate_limit_rpm: int | None = None
    dedup_strategy: DedupStrategy = DedupStrategy.EXACT
    max_review_seconds: int = Field(default=300, ge=10)
    max_pr_files: int = Field(default=200, ge=1)
    github_token: SecretStr | None = None
    github_rate_limit_warn_threshold: int = Field(default=100, ge=0)
    log_level: LogLevel = LogLevel.INFO
    max_concurrent_agents: int = 4

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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resolved_llm_base_url(self) -> str:
        """Return the base URL for the configured LLM provider.

        If ``llm_base_url`` is set explicitly, use it (escape hatch for custom
        providers). Otherwise map the known provider name to its URL.
        """
        if self.llm_base_url is not None:
            return self.llm_base_url
        return _PROVIDER_BASE_URLS[self.llm_provider]

    model_config = {
        "env_file": ".env",
        "env_prefix": "",
        "extra": "ignore",
    }
