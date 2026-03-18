"""Tests for BackgroundReview and non-blocking REPL integration."""

from __future__ import annotations

from unittest.mock import MagicMock

from code_review_agent.interactive.background import (
    _ICON_DONE,
    _ICON_FAILED,
    _ICON_WAITING,
    _SPINNER_FRAMES,
    BackgroundReview,
)
from code_review_agent.models import ReviewEvent


class TestBackgroundReview:
    """Tests for the BackgroundReview lifecycle and status rendering."""

    def _make_bg(
        self,
        *,
        run_return: object = None,
        run_side_effect: Exception | None = None,
    ) -> BackgroundReview:
        settings = MagicMock()
        settings.token_tier = "free"  # noqa: S105
        settings.max_prompt_tokens = None
        llm_client = MagicMock()

        review_input = MagicMock()
        agent_names = ["security", "performance"]

        bg = BackgroundReview(
            settings=settings,
            llm_client=llm_client,
            review_input=review_input,
            agent_names=agent_names,
            label="test review",
        )

        # Replace orchestrator.run with mock
        if run_side_effect:
            bg.orchestrator.run = MagicMock(side_effect=run_side_effect)
        else:
            bg.orchestrator.run = MagicMock(return_value=run_return)

        return bg

    def test_start_and_complete(self) -> None:
        mock_report = MagicMock()
        bg = self._make_bg(run_return=mock_report)

        bg.start()
        bg._done.wait(timeout=5)

        assert bg.is_done
        assert not bg.is_running

        report, error = bg.collect_result()
        assert report is mock_report
        assert error is None

    def test_error_captured(self) -> None:
        bg = self._make_bg(run_side_effect=RuntimeError("boom"))

        bg.start()
        bg._done.wait(timeout=5)

        assert bg.is_done
        report, error = bg.collect_result()
        assert report is None
        assert isinstance(error, RuntimeError)
        assert str(error) == "boom"

    def test_event_callback_updates_agent_states(self) -> None:
        bg = self._make_bg()

        bg(ReviewEvent.AGENT_STARTED, "security")
        assert bg._agent_states["security"].status == "running"

        bg(ReviewEvent.AGENT_COMPLETED, "security", 5.0)
        assert bg._agent_states["security"].status == "done"
        assert bg._agent_states["security"].elapsed == 5.0

        bg(ReviewEvent.AGENT_FAILED, "performance", 3.0)
        assert bg._agent_states["performance"].status == "failed"

    def test_event_callback_tracks_phase(self) -> None:
        bg = self._make_bg()

        bg(ReviewEvent.AGENT_STARTED, "security")
        assert bg._phase == "agents"

        bg(ReviewEvent.SYNTHESIS_STARTED, "synthesis")
        assert bg._phase == "synthesis"

        bg(ReviewEvent.VALIDATION_STARTED, "validation")
        assert bg._phase == "validation"

    def test_format_status_line_contains_label(self) -> None:
        bg = self._make_bg()
        line = bg.format_status_line()
        assert "test review" in line

    def test_format_status_line_shows_spinner(self) -> None:
        bg = self._make_bg()
        bg(ReviewEvent.AGENT_STARTED, "security")
        line = bg.format_status_line()
        # Should contain a spinner character from the spinner frames
        assert any(c in line for c in _SPINNER_FRAMES)

    def test_format_status_line_shows_done_icon(self) -> None:
        bg = self._make_bg()
        bg(ReviewEvent.AGENT_COMPLETED, "security", 5.0)
        line = bg.format_status_line()
        assert _ICON_DONE in line

    def test_format_status_line_shows_failed_icon(self) -> None:
        bg = self._make_bg()
        bg(ReviewEvent.AGENT_FAILED, "security", 3.0)
        line = bg.format_status_line()
        assert _ICON_FAILED in line

    def test_format_status_line_shows_waiting_icon(self) -> None:
        bg = self._make_bg()
        line = bg.format_status_line()
        assert _ICON_WAITING in line

    def test_format_status_line_shows_agent_count(self) -> None:
        bg = self._make_bg()
        bg(ReviewEvent.AGENT_COMPLETED, "security", 5.0)
        line = bg.format_status_line()
        assert "[1/2]" in line

    def test_is_running_before_start(self) -> None:
        bg = self._make_bg()
        # _done starts unset, so is_running=True even before thread starts
        assert bg.is_running is True
        assert bg.is_done is False

    def test_collect_result_clears_state(self) -> None:
        mock_report = MagicMock()
        bg = self._make_bg(run_return=mock_report)
        bg.start()
        bg._done.wait(timeout=5)

        report1, _ = bg.collect_result()
        assert report1 is mock_report

        report2, error2 = bg.collect_result()
        assert report2 is None
        assert error2 is None
