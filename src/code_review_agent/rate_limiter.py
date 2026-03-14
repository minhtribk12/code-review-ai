from __future__ import annotations

import threading
import time
from collections import deque
from typing import TYPE_CHECKING, Protocol

import structlog

if TYPE_CHECKING:
    from code_review_agent.config import Settings

from code_review_agent.token_budget import TokenTier

logger = structlog.get_logger(__name__)


class RateLimiter(Protocol):
    """Protocol for rate limiting LLM API calls."""

    def acquire(self) -> None:
        """Block until a request slot is available."""
        ...

    def update_from_retry_after(self, retry_after_seconds: float) -> None:
        """Adapt rate limit based on provider feedback (e.g., 429 response)."""
        ...


_TIER_RPM: dict[TokenTier, int] = {
    TokenTier.FREE: 5,
    TokenTier.STANDARD: 30,
    TokenTier.PREMIUM: 0,
}


class NoOpRateLimiter:
    """Rate limiter that does nothing. Used for unlimited tiers."""

    def acquire(self) -> None:
        pass

    def update_from_retry_after(self, retry_after_seconds: float) -> None:
        pass


class SlidingWindowRateLimiter:
    """Thread-safe sliding window rate limiter.

    Tracks request timestamps in a deque and blocks when the window is full.
    Adapts automatically when the provider signals a different limit via
    429 retry-after responses.
    """

    def __init__(self, max_requests: int, window_seconds: float = 60.0) -> None:
        self._max_requests = max_requests
        self._window = window_seconds
        self._timestamps: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until a request slot is available within the rate window."""
        while True:
            with self._lock:
                now = time.monotonic()

                # Evict expired timestamps
                while self._timestamps and self._timestamps[0] <= now - self._window:
                    self._timestamps.popleft()

                # Slot available
                if len(self._timestamps) < self._max_requests:
                    self._timestamps.append(now)
                    return

                # Calculate wait time until oldest slot expires
                wait = self._timestamps[0] + self._window - now

            logger.debug(
                "rate limit reached, waiting",
                wait_seconds=round(wait, 2),
                rpm=self._max_requests,
            )
            time.sleep(wait)

    def update_from_retry_after(self, retry_after_seconds: float) -> None:
        """Adapt rate limit based on provider retry-after feedback.

        If the provider tells us to wait N seconds, we can infer
        approximately how many requests per minute are allowed.
        """
        if retry_after_seconds <= 0:
            return

        # Infer RPM: if retry-after is 12s, that's ~5 req/min
        inferred_rpm = max(1, int(self._window / retry_after_seconds))

        with self._lock:
            if inferred_rpm < self._max_requests:
                logger.info(
                    "adapting rate limit from provider feedback",
                    previous_rpm=self._max_requests,
                    new_rpm=inferred_rpm,
                    retry_after=retry_after_seconds,
                )
                self._max_requests = inferred_rpm


def create_rate_limiter(settings: Settings) -> RateLimiter:
    """Create a rate limiter based on settings.

    Resolution hierarchy:
    1. ``rate_limit_rpm`` if set explicitly
    2. Tier preset (FREE=5, STANDARD=30, PREMIUM=unlimited)
    """
    if settings.rate_limit_rpm is not None:
        rpm = settings.rate_limit_rpm
    else:
        rpm = _TIER_RPM[settings.token_tier]

    if rpm <= 0:
        logger.debug("rate limiting disabled", rpm=rpm)
        return NoOpRateLimiter()

    logger.debug("rate limiter configured", rpm=rpm)
    return SlidingWindowRateLimiter(max_requests=rpm)
