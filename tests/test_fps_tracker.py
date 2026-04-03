"""Tests for FPS tracker and adaptive refresh."""

from __future__ import annotations

from code_review_agent.interactive.fps_tracker import FPSTracker


class TestFPSTracker:
    """Test FPS tracking and adaptive refresh."""

    def test_initial_state(self) -> None:
        tracker = FPSTracker()
        assert tracker.frame_count == 0
        assert tracker.p99_ms == 0.0
        assert tracker.avg_ms == 0.0

    def test_record_frame(self) -> None:
        tracker = FPSTracker()
        tracker.record_frame(10.0)
        tracker.record_frame(20.0)
        assert tracker.frame_count == 2
        assert tracker.avg_ms == 15.0

    def test_p99_calculation(self) -> None:
        tracker = FPSTracker(window_size=100)
        for i in range(100):
            tracker.record_frame(float(i))
        # P99 should be near 99
        assert tracker.p99_ms >= 95.0

    def test_slows_down_on_high_latency(self) -> None:
        tracker = FPSTracker(window_size=10)
        for _ in range(10):
            tracker.record_frame(60.0)  # 60ms per frame
        assert tracker.recommended_interval == 0.2

    def test_speeds_up_when_fast(self) -> None:
        tracker = FPSTracker(window_size=10)
        # First: make it slow
        for _ in range(10):
            tracker.record_frame(60.0)
        _ = tracker.recommended_interval  # trigger slow mode

        # Then: make it fast
        for _ in range(10):
            tracker.record_frame(5.0)
        assert tracker.recommended_interval == 0.1

    def test_stays_default_when_normal(self) -> None:
        tracker = FPSTracker(window_size=10)
        for _ in range(10):
            tracker.record_frame(30.0)  # 30ms = between thresholds
        assert tracker.recommended_interval == 0.1

    def test_window_eviction(self) -> None:
        tracker = FPSTracker(window_size=5)
        for _ in range(10):
            tracker.record_frame(10.0)
        assert tracker.frame_count == 5  # window capped

    def test_frame_context_manager(self) -> None:
        tracker = FPSTracker()
        with tracker.frame_context():
            _ = sum(range(100))
        assert tracker.frame_count == 1
        assert tracker.avg_ms >= 0.0

    def test_not_enough_frames_returns_default(self) -> None:
        tracker = FPSTracker(window_size=10)
        tracker.record_frame(100.0)  # Only 1 frame, need 5
        assert tracker.recommended_interval == 0.1
