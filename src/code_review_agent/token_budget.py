from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

import structlog

if TYPE_CHECKING:
    from code_review_agent.config import Settings

logger = structlog.get_logger(__name__)


class TokenTier(StrEnum):
    """Preset token budget tiers for different model context windows."""

    FREE = "free"
    STANDARD = "standard"
    PREMIUM = "premium"


_TIER_BUDGETS: dict[TokenTier, int] = {
    TokenTier.FREE: 5_000,
    TokenTier.STANDARD: 16_000,
    TokenTier.PREMIUM: 48_000,
}

# Default agents per tier. Free tier runs only security to minimize LLM calls.
_TIER_DEFAULT_AGENTS: dict[TokenTier, list[str]] = {
    TokenTier.FREE: ["security"],
    TokenTier.STANDARD: ["security", "performance", "style", "test_coverage"],
    TokenTier.PREMIUM: ["security", "performance", "style", "test_coverage"],
}


def default_agents_for_tier(tier: TokenTier) -> list[str]:
    """Return the default agent names for the given token tier."""
    return list(_TIER_DEFAULT_AGENTS[tier])


# Known model context windows (tokens).
# Used for auto-detection when max_prompt_tokens is not set explicitly.
_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    # NVIDIA
    "nvidia/nemotron-3-super-120b-a12b": 128_000,
    "nvidia/llama-3.1-nemotron-70b-instruct": 128_000,
    # Meta Llama
    "meta-llama/llama-3-8b-instruct": 8_192,
    "meta-llama/llama-3-70b-instruct": 8_192,
    "meta-llama/llama-3.1-8b-instruct": 128_000,
    "meta-llama/llama-3.1-70b-instruct": 128_000,
    # OpenAI
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-3.5-turbo": 16_385,
    # Mistral
    "mistralai/mistral-7b-instruct": 32_768,
    "mistralai/mixtral-8x7b-instruct": 32_768,
    # Google
    "google/gemma-2-9b-it": 8_192,
}

# Use 40% of context window for the user prompt (diff content).
# The remaining 60% is reserved for system prompt, schema, and response.
_CONTEXT_BUDGET_RATIO = 0.4


# Known model pricing: (input_price_per_M_tokens, output_price_per_M_tokens).
# Used for auto-detection when user does not provide custom pricing.
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    # NVIDIA (OpenRouter pricing)
    "nvidia/nemotron-3-super-120b-a12b": (0.30, 0.60),
    "nvidia/llama-3.1-nemotron-70b-instruct": (0.20, 0.40),
    # Meta Llama (OpenRouter pricing)
    "meta-llama/llama-3-8b-instruct": (0.06, 0.06),
    "meta-llama/llama-3-70b-instruct": (0.52, 0.75),
    "meta-llama/llama-3.1-8b-instruct": (0.05, 0.08),
    "meta-llama/llama-3.1-70b-instruct": (0.35, 0.40),
    # OpenAI
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.00, 30.00),
    "gpt-3.5-turbo": (0.50, 1.50),
    # Mistral
    "mistralai/mistral-7b-instruct": (0.06, 0.06),
    "mistralai/mixtral-8x7b-instruct": (0.24, 0.24),
    # Google
    "google/gemma-2-9b-it": (0.08, 0.08),
}


def estimate_cost(
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    input_price_per_m: float | None = None,
    output_price_per_m: float | None = None,
) -> float | None:
    """Estimate cost in USD for the given token usage.

    Resolution:
    1. If custom prices provided, use them (exact).
    2. If model is in _MODEL_PRICING, use auto-detected prices (estimated).
    3. Otherwise return None (unknown pricing).
    """
    if input_price_per_m is not None and output_price_per_m is not None:
        return (
            prompt_tokens * input_price_per_m + completion_tokens * output_price_per_m
        ) / 1_000_000

    pricing = _MODEL_PRICING.get(model)
    if pricing is not None:
        input_price, output_price = pricing
        return (prompt_tokens * input_price + completion_tokens * output_price) / 1_000_000

    return None


class TokenEstimator(Protocol):
    """Protocol for estimating token count from text."""

    def estimate(self, text: str) -> int: ...


class CharBasedEstimator:
    """Estimate tokens by dividing character count.

    Uses chars_per_token=3 by default, which intentionally overestimates
    by 10-20% for code.  This is safer than underestimating -- truncation
    is better than an API rejection for exceeding context limits.
    """

    def __init__(self, chars_per_token: int = 3) -> None:
        self._chars_per_token = chars_per_token

    def estimate(self, text: str) -> int:
        """Return estimated token count for the given text."""
        return len(text) // self._chars_per_token


def resolve_prompt_budget(settings: Settings) -> int:
    """Resolve the prompt token budget from settings.

    Resolution hierarchy (highest priority wins):
    1. ``max_prompt_tokens`` if set explicitly
    2. Auto-detect from model context window (known models registry)
    3. ``token_tier`` preset value
    4. Fallback: FREE tier (5000 tokens)
    """
    # 1. Explicit override
    if settings.max_prompt_tokens is not None:
        logger.debug(
            "using explicit max_prompt_tokens",
            budget=settings.max_prompt_tokens,
        )
        return settings.max_prompt_tokens

    # 2. Auto-detect from model
    context_window = _MODEL_CONTEXT_WINDOWS.get(settings.llm_model)
    if context_window is not None:
        budget = int(context_window * _CONTEXT_BUDGET_RATIO)
        logger.debug(
            "auto-detected prompt budget from model",
            model=settings.llm_model,
            context_window=context_window,
            budget=budget,
        )
        return budget

    # 3. Token tier preset
    budget = _TIER_BUDGETS[settings.token_tier]
    logger.debug(
        "using token tier budget",
        tier=settings.token_tier,
        budget=budget,
        model=settings.llm_model,
    )
    return budget
