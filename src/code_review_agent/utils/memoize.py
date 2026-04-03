"""Memoization decorators with TTL and LRU support.

Inspired by better-clawd's memoizeWithTTL and memoizeWithLRU patterns.
"""

from __future__ import annotations

import functools
import threading
import time
from collections import OrderedDict
from collections.abc import Callable  # noqa: TC003 - used at runtime in decorator signatures
from typing import TypeVar

T = TypeVar("T")


def memoize_with_ttl(ttl_seconds: float) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Cache function results with time-based expiration.

    After ``ttl_seconds``, the cached value is discarded and recomputed
    on the next call. Thread-safe.
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        cache: dict[tuple[object, ...], tuple[T, float]] = {}
        lock = threading.Lock()

        @functools.wraps(fn)
        def wrapper(*args: object, **kwargs: object) -> T:
            key = args + tuple(sorted(kwargs.items()))
            now = time.monotonic()
            with lock:
                if key in cache:
                    value, timestamp = cache[key]
                    if now - timestamp < ttl_seconds:
                        return value
            result = fn(*args, **kwargs)
            with lock:
                cache[key] = (result, now)
            return result

        def clear_cache() -> None:
            with lock:
                cache.clear()

        wrapper.clear_cache = clear_cache  # type: ignore[attr-defined]
        return wrapper

    return decorator


def memoize_with_lru(max_size: int = 128) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Cache function results with LRU eviction.

    Keeps at most ``max_size`` entries. Least recently used entries are
    evicted when the cache is full. Thread-safe.
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        cache: OrderedDict[tuple[object, ...], T] = OrderedDict()
        lock = threading.Lock()

        @functools.wraps(fn)
        def wrapper(*args: object, **kwargs: object) -> T:
            key = args + tuple(sorted(kwargs.items()))
            with lock:
                if key in cache:
                    cache.move_to_end(key)
                    return cache[key]
            result = fn(*args, **kwargs)
            with lock:
                cache[key] = result
                if len(cache) > max_size:
                    cache.popitem(last=False)
            return result

        def clear_cache() -> None:
            with lock:
                cache.clear()

        wrapper.clear_cache = clear_cache  # type: ignore[attr-defined]
        return wrapper

    return decorator
