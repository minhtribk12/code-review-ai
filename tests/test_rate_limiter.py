from __future__ import annotations

import threading
import time

import pytest  # noqa: TC002

from code_review_agent.rate_limiter import (
    _TIER_RPM,
    NoOpRateLimiter,
    SlidingWindowRateLimiter,
    create_rate_limiter,
)
from code_review_agent.token_budget import TokenTier

# ---------------------------------------------------------------------------
# NoOpRateLimiter
# ---------------------------------------------------------------------------


class TestNoOpRateLimiter:
    """NoOp limiter should never block."""

    def test_acquire_returns_immediately(self) -> None:
        limiter = NoOpRateLimiter()
        start = time.monotonic()
        for _ in range(100):
            limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.1

    def test_update_does_nothing(self) -> None:
        limiter = NoOpRateLimiter()
        limiter.update_from_retry_after(60.0)  # no error


# ---------------------------------------------------------------------------
# SlidingWindowRateLimiter
# ---------------------------------------------------------------------------


class TestSlidingWindowRateLimiter:
    """Test the sliding window rate limiter."""

    def test_under_limit_no_blocking(self) -> None:
        limiter = SlidingWindowRateLimiter(max_requests=10, window_seconds=60.0)
        start = time.monotonic()
        for _ in range(5):
            limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.1

    def test_at_limit_blocks(self) -> None:
        limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=0.5)
        limiter.acquire()
        limiter.acquire()
        # Third call should block until window expires (~0.5s)
        start = time.monotonic()
        limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.3  # allow some tolerance

    def test_window_expires_and_frees_slot(self) -> None:
        limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=0.3)
        limiter.acquire()
        # Wait for window to expire
        time.sleep(0.35)
        start = time.monotonic()
        limiter.acquire()  # should be immediate
        elapsed = time.monotonic() - start
        assert elapsed < 0.1

    def test_thread_safety(self) -> None:
        """Multiple threads should not exceed the rate limit."""
        limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=1.0)
        call_times: list[float] = []
        lock = threading.Lock()

        def worker() -> None:
            limiter.acquire()
            with lock:
                call_times.append(time.monotonic())

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(call_times) == 5


# ---------------------------------------------------------------------------
# Adaptive update
# ---------------------------------------------------------------------------


class TestAdaptiveUpdate:
    """Test rate limit adaptation from provider feedback."""

    def test_tighten_from_retry_after(self) -> None:
        limiter = SlidingWindowRateLimiter(max_requests=30, window_seconds=60.0)
        limiter.update_from_retry_after(12.0)
        # 60/12 = 5 RPM, which is less than 30, so it should tighten
        assert limiter._max_requests == 5

    def test_does_not_loosen(self) -> None:
        limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=60.0)
        limiter.update_from_retry_after(2.0)
        # 60/2 = 30 RPM, which is more than 5, so it should NOT change
        assert limiter._max_requests == 5

    def test_zero_retry_after_ignored(self) -> None:
        limiter = SlidingWindowRateLimiter(max_requests=10, window_seconds=60.0)
        limiter.update_from_retry_after(0.0)
        assert limiter._max_requests == 10

    def test_negative_retry_after_ignored(self) -> None:
        limiter = SlidingWindowRateLimiter(max_requests=10, window_seconds=60.0)
        limiter.update_from_retry_after(-5.0)
        assert limiter._max_requests == 10


# ---------------------------------------------------------------------------
# create_rate_limiter factory
# ---------------------------------------------------------------------------


class TestCreateRateLimiter:
    """Test factory function with settings."""

    def test_explicit_rpm_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_API_KEY", "sk-test-00000000")
        monkeypatch.setenv("LLM_PROVIDER", "openrouter")
        monkeypatch.setenv("RATE_LIMIT_RPM", "42")

        from code_review_agent.config import Settings

        settings = Settings()  # type: ignore[call-arg]
        limiter = create_rate_limiter(settings)
        assert isinstance(limiter, SlidingWindowRateLimiter)
        assert limiter._max_requests == 42

    def test_free_tier_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_API_KEY", "sk-test-00000000")
        monkeypatch.setenv("LLM_PROVIDER", "openrouter")
        monkeypatch.setenv("TOKEN_TIER", "free")
        monkeypatch.delenv("RATE_LIMIT_RPM", raising=False)

        from code_review_agent.config import Settings

        settings = Settings()  # type: ignore[call-arg]
        limiter = create_rate_limiter(settings)
        assert isinstance(limiter, SlidingWindowRateLimiter)
        assert limiter._max_requests == _TIER_RPM[TokenTier.FREE]

    def test_premium_tier_unlimited(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_API_KEY", "sk-test-00000000")
        monkeypatch.setenv("LLM_PROVIDER", "openrouter")
        monkeypatch.setenv("TOKEN_TIER", "premium")
        monkeypatch.delenv("RATE_LIMIT_RPM", raising=False)

        from code_review_agent.config import Settings

        settings = Settings()  # type: ignore[call-arg]
        limiter = create_rate_limiter(settings)
        assert isinstance(limiter, NoOpRateLimiter)

    def test_explicit_zero_rpm_unlimited(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_API_KEY", "sk-test-00000000")
        monkeypatch.setenv("LLM_PROVIDER", "openrouter")
        monkeypatch.setenv("RATE_LIMIT_RPM", "0")

        from code_review_agent.config import Settings

        settings = Settings()  # type: ignore[call-arg]
        limiter = create_rate_limiter(settings)
        assert isinstance(limiter, NoOpRateLimiter)


# ---------------------------------------------------------------------------
# Tier RPM values
# ---------------------------------------------------------------------------


class TestTierRpm:
    """Verify tier defaults."""

    def test_free_tier(self) -> None:
        assert _TIER_RPM[TokenTier.FREE] == 5

    def test_standard_tier(self) -> None:
        assert _TIER_RPM[TokenTier.STANDARD] == 30

    def test_premium_unlimited(self) -> None:
        assert _TIER_RPM[TokenTier.PREMIUM] == 0
