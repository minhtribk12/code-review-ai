"""Provider registry: loads provider metadata from bundled + user YAML files.

Resolution order (later wins):
1. Bundled defaults:  ``<package>/provider_registry.yaml``
2. User overrides:    ``~/.cra/providers.yaml``

Users can add entirely new providers or extend existing ones with extra
models by creating ``~/.cra/providers.yaml`` using the same schema.
When a provider key already exists in the bundled defaults, user-defined
models are appended and provider-level fields (base_url, default_model,
rate_limit_rpm) are overwritten if specified.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any  # YAML dicts from provider registry files have dynamic shape

import structlog
import yaml

logger = structlog.get_logger(__name__)

_BUNDLED_REGISTRY_PATH = Path(__file__).parent / "provider_registry.yaml"
_USER_REGISTRY_PATH = Path("~/.cra/providers.yaml").expanduser()


@dataclass(frozen=True)
class ModelInfo:
    """Metadata for a single model offered by a provider."""

    id: str
    name: str
    is_free: bool
    context_window: int


@dataclass(frozen=True)
class ProviderInfo:
    """Metadata for a single LLM provider."""

    base_url: str
    default_model: str
    rate_limit_rpm: int
    models: tuple[ModelInfo, ...] = field(default_factory=tuple)

    @property
    def free_models(self) -> list[ModelInfo]:
        """Return only the free models for this provider."""
        return [m for m in self.models if m.is_free]

    def model_ids(self, *, free_only: bool = False) -> list[str]:
        """Return model IDs, optionally filtered to free-only."""
        if free_only:
            return [m.id for m in self.models if m.is_free]
        return [m.id for m in self.models]


def _parse_models(raw_models: list[dict[str, Any]]) -> tuple[ModelInfo, ...]:
    """Parse a list of raw model dicts into ModelInfo tuples."""
    return tuple(
        ModelInfo(
            id=m["id"],
            name=m["name"],
            is_free=m.get("is_free", False),
            context_window=m.get("context_window", 128_000),
        )
        for m in raw_models
    )


def _parse_provider(data: dict[str, Any]) -> ProviderInfo:
    """Parse a single provider dict into ProviderInfo."""
    return ProviderInfo(
        base_url=data["base_url"],
        default_model=data["default_model"],
        rate_limit_rpm=data.get("rate_limit_rpm", 10),
        models=_parse_models(data.get("models", [])),
    )


def _merge_providers(
    base: dict[str, ProviderInfo],
    overlay: dict[str, Any],
) -> dict[str, ProviderInfo]:
    """Merge user overlay providers on top of base providers.

    For existing providers: appends new models (deduped by id), overwrites
    base_url / default_model / rate_limit_rpm if present in overlay.
    For new providers: adds them directly.
    """
    result = dict(base)

    for key, user_data in overlay.items():
        if key not in result:
            result[key] = _parse_provider(user_data)
            continue

        existing = result[key]

        # Merge models: existing + new (dedup by id)
        existing_ids = {m.id for m in existing.models}
        new_models = tuple(
            m for m in _parse_models(user_data.get("models", [])) if m.id not in existing_ids
        )
        merged_models = existing.models + new_models

        result[key] = ProviderInfo(
            base_url=user_data.get("base_url", existing.base_url),
            default_model=user_data.get("default_model", existing.default_model),
            rate_limit_rpm=user_data.get("rate_limit_rpm", existing.rate_limit_rpm),
            models=merged_models,
        )

    return result


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load and return the 'providers' dict from a registry YAML file."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}
    result: dict[str, Any] = raw.get("providers", {})
    return result


def _load_registry() -> dict[str, ProviderInfo]:
    """Load bundled defaults, then merge user overrides on top."""
    # 1. Bundled defaults (must exist)
    bundled_raw = _load_yaml(_BUNDLED_REGISTRY_PATH)
    result: dict[str, ProviderInfo] = {}
    for key, data in bundled_raw.items():
        result[key] = _parse_provider(data)

    # 2. User overrides (optional)
    # NOTE: This runs at import time (before structlog is configured).
    # Logging here would leak debug messages to the terminal, so we
    # guard with structlog.is_configured() before logging.
    if _USER_REGISTRY_PATH.is_file():
        try:
            user_raw = _load_yaml(_USER_REGISTRY_PATH)
            result = _merge_providers(result, user_raw)
            if structlog.is_configured():
                logger.debug(
                    "loaded user provider overrides",
                    path=str(_USER_REGISTRY_PATH),
                    providers=list(user_raw.keys()),
                )
        except Exception:
            if structlog.is_configured():
                logger.debug(
                    "failed to load user provider registry",
                    path=str(_USER_REGISTRY_PATH),
                    exc_info=True,
                )

    return result


# Module-level singleton: loaded once at import time.
PROVIDER_REGISTRY: dict[str, ProviderInfo] = _load_registry()


def reload_registry() -> None:
    """Reload the provider registry from disk (bundled + user files)."""
    global PROVIDER_REGISTRY
    PROVIDER_REGISTRY = _load_registry()


def get_provider(provider: str) -> ProviderInfo:
    """Look up provider info by key. Raises KeyError if unknown."""
    return PROVIDER_REGISTRY[provider]


def get_base_url(provider: str) -> str:
    """Return the base URL for a known provider."""
    return PROVIDER_REGISTRY[provider].base_url


def get_default_model(provider: str) -> str:
    """Return the default free model for a known provider."""
    return PROVIDER_REGISTRY[provider].default_model


def get_context_window(model_id: str) -> int | None:
    """Look up context window for a model across all providers."""
    for info in PROVIDER_REGISTRY.values():
        for m in info.models:
            if m.id == model_id:
                return m.context_window
    return None
