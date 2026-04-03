"""Tests for memoization decorators."""

from __future__ import annotations

import time

from code_review_agent.utils.memoize import memoize_with_lru, memoize_with_ttl


class TestMemoizeWithTTL:
    """Test time-based cache expiration."""

    def test_caches_result(self) -> None:
        call_count = 0

        @memoize_with_ttl(ttl_seconds=10.0)
        def compute(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x * 2

        assert compute(5) == 10
        assert compute(5) == 10
        assert call_count == 1

    def test_different_args_cached_separately(self) -> None:
        call_count = 0

        @memoize_with_ttl(ttl_seconds=10.0)
        def compute(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x * 2

        assert compute(1) == 2
        assert compute(2) == 4
        assert call_count == 2

    def test_expires_after_ttl(self) -> None:
        call_count = 0

        @memoize_with_ttl(ttl_seconds=0.05)
        def compute(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x

        compute(1)
        assert call_count == 1
        time.sleep(0.1)
        compute(1)
        assert call_count == 2

    def test_clear_cache(self) -> None:
        call_count = 0

        @memoize_with_ttl(ttl_seconds=10.0)
        def compute(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x

        compute(1)
        compute.clear_cache()  # type: ignore[attr-defined]
        compute(1)
        assert call_count == 2


class TestMemoizeWithLRU:
    """Test LRU cache with eviction."""

    def test_caches_result(self) -> None:
        call_count = 0

        @memoize_with_lru(max_size=3)
        def compute(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x * 2

        assert compute(5) == 10
        assert compute(5) == 10
        assert call_count == 1

    def test_evicts_lru_entry(self) -> None:
        call_count = 0

        @memoize_with_lru(max_size=2)
        def compute(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x

        compute(1)  # cache: [1]
        compute(2)  # cache: [1, 2]
        compute(3)  # cache: [2, 3] -- 1 evicted
        assert call_count == 3

        compute(1)  # cache: [3, 1] -- recomputed
        assert call_count == 4

        compute(3)  # cache: [1, 3] -- still cached
        assert call_count == 4

    def test_clear_cache(self) -> None:
        call_count = 0

        @memoize_with_lru(max_size=10)
        def compute(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x

        compute(1)
        compute.clear_cache()  # type: ignore[attr-defined]
        compute(1)
        assert call_count == 2

    def test_kwargs_support(self) -> None:
        call_count = 0

        @memoize_with_lru(max_size=10)
        def compute(x: int, multiplier: int = 1) -> int:
            nonlocal call_count
            call_count += 1
            return x * multiplier

        assert compute(2, multiplier=3) == 6
        assert compute(2, multiplier=3) == 6
        assert call_count == 1
        assert compute(2, multiplier=5) == 10
        assert call_count == 2
