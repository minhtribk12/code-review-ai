from __future__ import annotations

from pydantic import SecretStr, computed_field
from pydantic_settings import BaseSettings

_PROVIDER_BASE_URLS: dict[str, str] = {
    "openrouter": "https://openrouter.ai/api/v1",
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "openai": "https://api.openai.com/v1",
}


class Settings(BaseSettings):
    """Application configuration loaded from environment variables and .env file."""

    llm_provider: str = "openrouter"
    llm_api_key: SecretStr
    llm_model: str = "nvidia/nemotron-3-super-120b-a12b"
    github_token: SecretStr | None = None
    log_level: str = "INFO"
    max_concurrent_agents: int = 4

    @computed_field  # type: ignore[prop-decorator]
    @property
    def llm_base_url(self) -> str:
        """Return the base URL for the configured LLM provider."""
        provider = self.llm_provider.lower()
        if provider in _PROVIDER_BASE_URLS:
            return _PROVIDER_BASE_URLS[provider]
        # Assume the provider string is a custom base URL.
        return provider

    model_config = {
        "env_file": ".env",
        "env_prefix": "",
        "extra": "ignore",
    }
