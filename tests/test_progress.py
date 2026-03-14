from __future__ import annotations

from unittest.mock import patch

from code_review_agent.models import ReviewEvent
from code_review_agent.progress import (
    NoOpCallback,
    ProgressDisplay,
    create_progress_callback,
)


class TestNoOpCallback:
    """NoOpCallback should accept events without error."""

    def test_accepts_all_events(self) -> None:
        callback = NoOpCallback()
        for event in ReviewEvent:
            callback(event, "test_agent", elapsed=1.0)

    def test_accepts_none_elapsed(self) -> None:
        callback = NoOpCallback()
        callback(ReviewEvent.AGENT_STARTED, "security")


class TestProgressDisplay:
    """Test ProgressDisplay state tracking."""

    def test_initial_states_are_waiting(self) -> None:
        display = ProgressDisplay(agent_names=["security", "style"])
        assert display._states["security"] == ("waiting", "dim", None)
        assert display._states["style"] == ("waiting", "dim", None)

    def test_agent_started_sets_running(self) -> None:
        display = ProgressDisplay(agent_names=["security"])
        display(ReviewEvent.AGENT_STARTED, "security")
        assert display._states["security"][0] == "running"

    def test_agent_completed_sets_done(self) -> None:
        display = ProgressDisplay(agent_names=["security"])
        display(ReviewEvent.AGENT_STARTED, "security")
        display(ReviewEvent.AGENT_COMPLETED, "security", elapsed=2.5)
        state, color, elapsed = display._states["security"]
        assert state == "done"
        assert color == "green"
        assert elapsed == 2.5

    def test_agent_failed_sets_failed(self) -> None:
        display = ProgressDisplay(agent_names=["security"])
        display(ReviewEvent.AGENT_STARTED, "security")
        display(ReviewEvent.AGENT_FAILED, "security", elapsed=5.0)
        state, color, elapsed = display._states["security"]
        assert state == "failed"
        assert color == "red"
        assert elapsed == 5.0

    def test_synthesis_events(self) -> None:
        display = ProgressDisplay(agent_names=["security"])
        display(ReviewEvent.SYNTHESIS_STARTED, "synthesis")
        assert display._states["synthesis"][0] == "running"

        display(ReviewEvent.SYNTHESIS_COMPLETED, "synthesis", elapsed=1.2)
        state, _color, elapsed = display._states["synthesis"]
        assert state == "done"
        assert elapsed == 1.2

    def test_build_table_includes_all_agents(self) -> None:
        display = ProgressDisplay(agent_names=["security", "performance", "style"])
        table = display._build_table()
        assert table.row_count == 3

    def test_build_table_includes_synthesis_when_present(self) -> None:
        display = ProgressDisplay(agent_names=["security"])
        display(ReviewEvent.SYNTHESIS_STARTED, "synthesis")
        table = display._build_table()
        assert table.row_count == 2

    def test_format_status_running(self) -> None:
        result = ProgressDisplay._format_status("running", "blue")
        assert "running" in result
        assert "blue" in result

    def test_format_status_done(self) -> None:
        result = ProgressDisplay._format_status("done", "green")
        assert "done" in result

    def test_format_status_failed(self) -> None:
        result = ProgressDisplay._format_status("failed", "red")
        assert "failed" in result

    def test_format_status_waiting(self) -> None:
        result = ProgressDisplay._format_status("waiting", "dim")
        assert "waiting" in result


class TestCreateProgressCallback:
    """Test factory function for progress callbacks."""

    def test_quiet_mode_returns_noop(self) -> None:
        callback, display = create_progress_callback(agent_names=["security"], is_quiet=True)
        assert isinstance(callback, NoOpCallback)
        assert display is None

    def test_non_interactive_returns_noop(self) -> None:
        with patch("code_review_agent.progress.is_interactive", return_value=False):
            callback, display = create_progress_callback(agent_names=["security"], is_quiet=False)
        assert isinstance(callback, NoOpCallback)
        assert display is None

    def test_interactive_returns_progress_display(self) -> None:
        with patch("code_review_agent.progress.is_interactive", return_value=True):
            callback, display = create_progress_callback(
                agent_names=["security", "style"], is_quiet=False
            )
        assert isinstance(callback, ProgressDisplay)
        assert display is callback


class TestMultiAgentProgress:
    """Test realistic multi-agent progress sequences."""

    def test_parallel_agent_lifecycle(self) -> None:
        """Simulate parallel agents starting and completing at different times."""
        agents = ["security", "performance", "style", "test_coverage"]
        display = ProgressDisplay(agent_names=agents)

        # All start
        for name in agents:
            display(ReviewEvent.AGENT_STARTED, name)
        assert all(display._states[n][0] == "running" for n in agents)

        # Some complete, one fails
        display(ReviewEvent.AGENT_COMPLETED, "security", elapsed=2.0)
        display(ReviewEvent.AGENT_COMPLETED, "style", elapsed=3.0)
        display(ReviewEvent.AGENT_FAILED, "performance", elapsed=5.0)
        display(ReviewEvent.AGENT_COMPLETED, "test_coverage", elapsed=4.0)

        assert display._states["security"][0] == "done"
        assert display._states["style"][0] == "done"
        assert display._states["performance"][0] == "failed"
        assert display._states["test_coverage"][0] == "done"

        # Synthesis
        display(ReviewEvent.SYNTHESIS_STARTED, "synthesis")
        assert display._states["synthesis"][0] == "running"
        display(ReviewEvent.SYNTHESIS_COMPLETED, "synthesis", elapsed=1.5)
        assert display._states["synthesis"][0] == "done"

        # Final table should have 5 rows (4 agents + synthesis)
        table = display._build_table()
        assert table.row_count == 5
