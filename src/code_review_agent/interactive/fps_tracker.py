"""FPS tracker for adaptive refresh rate.

Tracks per-frame render duration and adapts refresh_interval based on
performance. If rendering is slow (P99 > 50ms), increases interval.
If fast (P99 < 20ms), restores default.
"""

from __future__ import annotations

import time
from collections import deque

import structlog

logger = structlog.get_logger(__name__)

_DEFAULT_REFRESH = 0.1
_SLOW_REFRESH = 0.2
_FAST_THRESHOLD_MS = 20.0
_SLOW_THRESHOLD_MS = 50.0
_WINDOW_SIZE = 20


class FPSTracker:
    """Track render frame durations and recommend refresh interval."""

    def __init__(self, window_size: int = _WINDOW_SIZE) -> None:
        self._durations: deque[float] = deque(maxlen=window_size)
        self._current_interval: float = _DEFAULT_REFRESH

    def record_frame(self, duration_ms: float) -> None:
        """Record a single frame render duration in milliseconds."""
        self._durations.append(duration_ms)

    def frame_context(self) -> _FrameTimer:
        """Context manager that automatically records frame duration."""
        return _FrameTimer(self)

    @property
    def p99_ms(self) -> float:
        """Return the P99 render duration in milliseconds."""
        if not self._durations:
            return 0.0
        sorted_d = sorted(self._durations)
        idx = max(0, int(len(sorted_d) * 0.99) - 1)
        return sorted_d[idx]

    @property
    def avg_ms(self) -> float:
        """Return the average render duration in milliseconds."""
        if not self._durations:
            return 0.0
        return sum(self._durations) / len(self._durations)

    @property
    def recommended_interval(self) -> float:
        """Return the recommended refresh interval based on performance."""
        if len(self._durations) < 5:
            return self._current_interval
        p99 = self.p99_ms
        if p99 > _SLOW_THRESHOLD_MS and self._current_interval == _DEFAULT_REFRESH:
            self._current_interval = _SLOW_REFRESH
            logger.debug(
                f"render P99={p99:.1f}ms > {_SLOW_THRESHOLD_MS}ms, "
                f"slowing refresh to {_SLOW_REFRESH}s"
            )
        elif p99 < _FAST_THRESHOLD_MS and self._current_interval == _SLOW_REFRESH:
            self._current_interval = _DEFAULT_REFRESH
            logger.debug(
                f"render P99={p99:.1f}ms < {_FAST_THRESHOLD_MS}ms, "
                f"restoring refresh to {_DEFAULT_REFRESH}s"
            )
        return self._current_interval

    @property
    def frame_count(self) -> int:
        """Return the number of frames recorded."""
        return len(self._durations)


class _FrameTimer:
    """Context manager for timing a render frame."""

    def __init__(self, tracker: FPSTracker) -> None:
        self._tracker = tracker
        self._start: float = 0.0

    def __enter__(self) -> _FrameTimer:
        self._start = time.perf_counter()
        return self

    def __exit__(self, *args: object) -> None:
        elapsed_ms = (time.perf_counter() - self._start) * 1000
        self._tracker.record_frame(elapsed_ms)
