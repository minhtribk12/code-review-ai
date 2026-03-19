"""Session state for the interactive REPL."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from code_review_agent.config import Settings
    from code_review_agent.config_store import ConfigStore, SecretsStore
    from code_review_agent.interactive.background import BackgroundReview
    from code_review_agent.models import ReviewReport

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class PRCache:
    """Cache for PR list data to avoid redundant GitHub API calls.

    Stores the most recent list_prs result with a monotonic timestamp.
    Invalidated explicitly on PR write operations (create/merge/approve)
    or when the TTL expires.
    """

    data: list[dict[str, Any]] = field(default_factory=list)
    owner: str = ""
    repo: str = ""
    state: str = ""
    fetched_at: float = 0.0
    ttl_seconds: float = 60.0

    @property
    def is_valid(self) -> bool:
        """Return True if cached data is still fresh."""
        if not self.data:
            return False
        return (time.monotonic() - self.fetched_at) < self.ttl_seconds

    def get(self, owner: str, repo: str, state: str) -> list[dict[str, Any]] | None:
        """Return cached data if it matches the query and is still fresh."""
        if self.is_valid and self.owner == owner and self.repo == repo and self.state == state:
            return self.data
        return None

    def set(
        self,
        owner: str,
        repo: str,
        state: str,
        data: list[dict[str, Any]],
    ) -> None:
        """Store PR list data in cache."""
        self.owner = owner
        self.repo = repo
        self.state = state
        self.data = data
        self.fetched_at = time.monotonic()

    def invalidate(self) -> None:
        """Clear cached data (call after PR write operations)."""
        self.data = []
        self.fetched_at = 0.0


@dataclass(frozen=True)
class ReviewRecord:
    """Record of a single review for usage tracking."""

    timestamp: float  # time.time() (wall clock, for time-window queries)
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int
    llm_calls: int
    estimated_cost_usd: float | None
    agent_tokens: dict[str, int]  # agent_name -> tokens used
    agent_names: list[str]


@dataclass
class UsageHistory:
    """Tracks per-review usage records for time-window and per-agent queries."""

    records: list[ReviewRecord] = field(default_factory=list)

    def record_review(self, report: ReviewReport) -> None:
        """Record usage from a completed review report."""
        agent_tokens: dict[str, int] = {}
        agent_names: list[str] = []
        for result in report.agent_results:
            agent_names.append(result.agent_name)
            # Per-agent token tracking requires LLM client data not in AgentResult.
            # Estimate proportionally from total usage and execution time.
            agent_tokens[result.agent_name] = 0

        total_tokens = 0
        prompt_tokens = 0
        completion_tokens = 0
        llm_calls = 0
        estimated_cost: float | None = None

        if report.token_usage is not None:
            total_tokens = report.token_usage.total_tokens
            prompt_tokens = report.token_usage.prompt_tokens
            completion_tokens = report.token_usage.completion_tokens
            llm_calls = report.token_usage.llm_calls
            estimated_cost = report.token_usage.estimated_cost_usd

            # Distribute tokens proportionally by execution time
            total_time = sum(r.execution_time_seconds for r in report.agent_results)
            if total_time > 0 and len(report.agent_results) > 0:
                # Reserve synthesis tokens (last LLM call if multiple agents)
                synthesis_share = total_tokens // max(llm_calls, 1)
                has_synthesis = llm_calls > len(agent_names)
                agent_pool = total_tokens - synthesis_share if has_synthesis else total_tokens
                for result in report.agent_results:
                    share = result.execution_time_seconds / total_time
                    agent_tokens[result.agent_name] = int(agent_pool * share)

        self.records.append(
            ReviewRecord(
                timestamp=time.time(),
                total_tokens=total_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                llm_calls=llm_calls,
                estimated_cost_usd=estimated_cost,
                agent_tokens=agent_tokens,
                agent_names=agent_names,
            )
        )

    def records_since(self, seconds_ago: float) -> list[ReviewRecord]:
        """Return records within the last N seconds."""
        cutoff = time.time() - seconds_ago
        return [r for r in self.records if r.timestamp >= cutoff]

    @property
    def total_tokens(self) -> int:
        return sum(r.total_tokens for r in self.records)

    @property
    def total_cost(self) -> float:
        return sum(r.estimated_cost_usd or 0.0 for r in self.records)

    @property
    def total_calls(self) -> int:
        return sum(r.llm_calls for r in self.records)

    def tokens_by_agent(self) -> dict[str, int]:
        """Aggregate token usage per agent across all reviews."""
        totals: dict[str, int] = {}
        for record in self.records:
            for agent, tokens in record.agent_tokens.items():
                totals[agent] = totals.get(agent, 0) + tokens
        return totals


@dataclass
class SessionState:
    """Mutable state that persists across REPL commands within a session.

    Holds a mutable copy of settings (for session-only overrides),
    usage counters, and context tracking.
    """

    settings: Settings
    config_store: ConfigStore | None = None
    secrets_store: SecretsStore | None = None
    reviews_completed: int = 0
    total_tokens_used: int = 0
    current_context: str = "default"
    config_overrides: dict[str, str] = field(default_factory=dict)
    pr_cache: PRCache = field(default_factory=PRCache)
    usage_history: UsageHistory = field(default_factory=UsageHistory)
    active_repo: str | None = None  # "owner/repo" for PR commands
    active_repo_source: str = ""  # "local" or "remote"
    last_review_report: ReviewReport | None = None
    last_review_id: int | None = None
    background_review: BackgroundReview | None = None
    command_queue: list[str] = field(default_factory=list)
    _effective_settings_cache: Settings | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def _get_secrets_store(self) -> SecretsStore:
        """Return the secrets store, creating a default if not set."""
        if self.secrets_store is not None:
            return self.secrets_store
        from code_review_agent.config_store import SecretsStore as _SecretsStore

        self.secrets_store = _SecretsStore()
        return self.secrets_store

    def _get_config_store(self) -> ConfigStore:
        """Return the config store, creating a default if not set."""
        if self.config_store is not None:
            return self.config_store
        from code_review_agent.config_store import ConfigStore as _ConfigStore

        self.config_store = _ConfigStore()
        return self.config_store

    def _inject_secrets_to_env(self) -> None:
        """Load all API keys from secrets.env into environment variables.

        secrets.env is the primary source of truth for API keys. Values
        always overwrite environment variables so that
        ``Settings.resolve_api_key_for()`` picks them up.
        """
        try:
            self._get_secrets_store().inject_to_env()
        except Exception:
            logger.debug("failed to inject secrets.env API keys to env", exc_info=True)

    @property
    def effective_settings(self) -> Settings:
        """Return settings with session overrides applied.

        Uses ``model_copy(update=...)`` to patch overrides onto the base
        settings. Each override value is validated through Pydantic's
        ``TypeAdapter`` to ensure correct types. Invalid overrides are
        skipped with a warning.
        """
        if not self.config_overrides:
            self._effective_settings_cache = None
            return self.settings

        if self._effective_settings_cache is not None:
            return self._effective_settings_cache

        import typing

        from pydantic import TypeAdapter

        from code_review_agent.config import Settings

        resolved_hints = typing.get_type_hints(Settings)
        validated_updates: dict[str, object] = {}

        for key, raw_val in self.config_overrides.items():
            if key not in Settings.model_fields:
                continue

            # Convert "None" / "" to None for optional fields
            if raw_val in ("None", ""):
                validated_updates[key] = None
                continue

            # Coerce value through the field's type annotation
            hint = resolved_hints.get(key)
            if hint is not None:
                try:
                    adapter = TypeAdapter(hint)
                    validated_updates[key] = adapter.validate_python(raw_val)
                except Exception:
                    logger.debug(
                        "skipping invalid config override",
                        key=key,
                        value=raw_val,
                    )
            else:
                validated_updates[key] = raw_val

        if not validated_updates:
            return self.settings

        self._inject_secrets_to_env()

        try:
            rebuilt = self.settings.model_copy(update=validated_updates)
            self._effective_settings_cache = rebuilt
            return rebuilt
        except Exception as exc:
            logger.debug(
                "failed to apply config overrides",
                error=str(exc),
            )
            return self.settings

    def invalidate_settings_cache(self) -> None:
        """Clear the effective settings cache (call after modifying overrides)."""
        self._effective_settings_cache = None

    def resolve_api_key_display(self, provider: str | None = None) -> str:
        """Resolve the API key value for display.

        Two-source model (secrets.env is primary, .env is fallback):
        1. secrets.env (highest priority, app-managed)
        2. .env / environment variables (user-managed fallback)

        Returns the raw key string, or empty string if not found.
        """
        if provider is None:
            provider = self.config_overrides.get(
                "llm_provider",
                str(self.settings.llm_provider),
            )
        secrets_val = self.load_api_key_from_secrets(provider)
        if secrets_val:
            return secrets_val
        return self.load_api_key_from_env(provider)

    def load_api_key_from_secrets(self, provider: str) -> str:
        """Load an API key from secrets.env."""
        try:
            return self._get_secrets_store().load_key(provider)
        except Exception:
            return ""

    def load_api_key_from_env(self, provider: str) -> str:
        """Load an API key from .env / environment variables."""
        import os

        from pydantic import SecretStr

        real_key = f"{provider}_api_key"  # pragma: allowlist secret

        # Settings fields (.env loaded by pydantic)
        raw = getattr(self.settings, real_key, None)
        if isinstance(raw, SecretStr):
            val = raw.get_secret_value()
            if val and val != "__placeholder__":
                return val

        # Direct env var (for custom providers not in Settings model)
        env_key = f"{provider.upper()}_API_KEY"
        env_val = os.environ.get(env_key, "")
        if env_val and env_val != "__placeholder__":
            return env_val

        return ""

    def save_api_key(self, provider: str, value: str) -> None:
        """Save an API key to secrets.env and inject into env."""
        self._get_secrets_store().save_key(provider, value)
        self.invalidate_settings_cache()

    def delete_api_key(self, provider: str) -> None:
        """Delete an API key from secrets.env and clear env."""
        self._get_secrets_store().delete_key(provider)
        self.invalidate_settings_cache()

    @property
    def display_tier(self) -> str:
        """Return the tier label for display, showing 'custom' when overridden.

        The tier is a shortcut preset that sets default_agents and
        max_prompt_tokens. If the user overrides either of those, the
        tier no longer reflects the actual config, so we show 'custom'.
        """
        effective = self.effective_settings
        tier = effective.token_tier

        # If default_agents is explicitly set (non-empty), it overrides
        # the tier's agent defaults -> show as custom
        if effective.default_agents:
            from code_review_agent.token_budget import default_agents_for_tier

            tier_agents = sorted(default_agents_for_tier(tier))
            user_agents = sorted(
                n.strip() for n in effective.default_agents.split(",") if n.strip()
            )
            if user_agents != tier_agents:
                return f"{tier} (custom)"

        # If max_prompt_tokens overrides the tier budget
        if effective.max_prompt_tokens is not None:
            from code_review_agent.token_budget import _TIER_BUDGETS

            tier_budget = _TIER_BUDGETS.get(tier)
            if tier_budget is not None and effective.max_prompt_tokens != tier_budget:
                return f"{tier} (custom)"

        return str(tier)

    @property
    def has_cost_warning(self) -> bool:
        """Return True if cost-increasing overrides are active."""
        effective = self.effective_settings
        return effective.max_deepening_rounds > 1 or effective.is_validation_enabled

    def estimate_cost_multiplier(self) -> tuple[float, list[str]]:
        """Estimate the cost multiplier vs baseline and list reasons.

        Returns (multiplier, reasons) where multiplier is relative to
        a single-round no-validation review.
        """
        effective = self.effective_settings
        reasons: list[str] = []

        # Baseline: N agents + 1 synthesis = multiplier 1.0
        multiplier = 1.0

        # Deepening: each extra round re-runs all agents
        rounds = effective.max_deepening_rounds
        if rounds > 1:
            multiplier = float(rounds)
            reasons.append(f"max_deepening_rounds={rounds} ({rounds}x agent cost)")

        # Validation: adds LLM calls
        if effective.is_validation_enabled:
            val_rounds = effective.max_validation_rounds
            # Each validation round is ~1 LLM call relative to base
            # Approximate: +0.2 per validation round (1 call vs ~5 agent calls)
            val_cost = 0.2 * val_rounds
            multiplier += val_cost
            reasons.append(
                f"validation enabled ({val_rounds} round(s), +{val_rounds} LLM call(s))"
            )

        return multiplier, reasons
