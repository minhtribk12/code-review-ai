"""Tests for render caching, dirty tracking, mouse support, and filter caching."""

from __future__ import annotations

from unittest.mock import MagicMock

from code_review_agent.interactive.commands.findings.models import (
    FindingRow,
    ViewerMode,
)
from code_review_agent.interactive.commands.findings.state import FindingsViewer
from code_review_agent.models import Confidence, Severity


def _make_row(index: int, title: str = "test") -> FindingRow:
    return FindingRow(
        index=index,
        agent_name="security",
        severity=Severity.HIGH,
        title=title,
        description="desc",
        file_path="test.py",
        line_number=index,
        suggestion="fix it",
        confidence=Confidence.HIGH,
        repo=None,
        pr_number=None,
        triage_action="open",
        is_posted=False,
        comment_id=None,
        reviewed_at=None,
        finding_db_id=index,
    )


class TestDirtyTracking:
    """Test render generation / dirty flag mechanism."""

    def test_initial_state_is_dirty(self) -> None:
        viewer = FindingsViewer(rows=[_make_row(1)])
        assert viewer.is_render_dirty()

    def test_after_check_not_dirty(self) -> None:
        viewer = FindingsViewer(rows=[_make_row(1)])
        viewer.is_render_dirty()  # consume
        assert not viewer.is_render_dirty()

    def test_move_up_marks_dirty(self) -> None:
        viewer = FindingsViewer(rows=[_make_row(1), _make_row(2)])
        viewer.cursor = 1
        viewer.is_render_dirty()  # consume
        viewer.move_up()
        assert viewer.is_render_dirty()

    def test_move_down_marks_dirty(self) -> None:
        viewer = FindingsViewer(rows=[_make_row(1), _make_row(2)])
        viewer.is_render_dirty()
        viewer.move_down()
        assert viewer.is_render_dirty()

    def test_open_detail_marks_dirty(self) -> None:
        viewer = FindingsViewer(rows=[_make_row(1)])
        viewer.is_render_dirty()
        viewer.open_detail()
        assert viewer.is_render_dirty()
        assert viewer.mode == ViewerMode.DETAIL

    def test_scroll_marks_dirty(self) -> None:
        viewer = FindingsViewer(rows=[_make_row(1)])
        viewer.is_render_dirty()
        viewer.scroll_right()
        assert viewer.is_render_dirty()


class TestMouseCursorMove:
    """Test move_cursor_to for mouse click support."""

    def test_move_to_valid_row(self) -> None:
        viewer = FindingsViewer(rows=[_make_row(i) for i in range(5)])
        viewer.move_cursor_to(3)
        assert viewer.cursor == 3

    def test_move_to_out_of_range_clamped(self) -> None:
        viewer = FindingsViewer(rows=[_make_row(i) for i in range(3)])
        viewer.move_cursor_to(99)
        assert viewer.cursor == 2  # clamped to last row

    def test_move_to_negative_clamped(self) -> None:
        viewer = FindingsViewer(rows=[_make_row(i) for i in range(3)])
        viewer.move_cursor_to(-5)
        assert viewer.cursor == 0

    def test_move_marks_dirty(self) -> None:
        viewer = FindingsViewer(rows=[_make_row(i) for i in range(3)])
        viewer.is_render_dirty()
        viewer.move_cursor_to(2)
        assert viewer.is_render_dirty()


class TestFilterSuggestionCache:
    """Test that filter suggestions are cached per field."""

    def test_caches_db_query(self) -> None:
        mock_storage = MagicMock()
        mock_storage.get_distinct_finding_values.return_value = ["high", "medium", "low"]

        viewer = FindingsViewer(rows=[], storage=mock_storage)

        # First call should query DB
        result1 = viewer.get_filter_suggestions("severity", "")
        assert result1 == ["high", "medium", "low"]
        assert mock_storage.get_distinct_finding_values.call_count == 1

        # Second call should use cache, not query DB again
        result2 = viewer.get_filter_suggestions("severity", "h")
        assert result2 == ["high"]
        assert mock_storage.get_distinct_finding_values.call_count == 1

    def test_different_fields_cached_separately(self) -> None:
        mock_storage = MagicMock()
        mock_storage.get_distinct_finding_values.side_effect = lambda f: {
            "severity": ["high"],
            "agent_name": ["security"],
        }.get(f, [])

        viewer = FindingsViewer(rows=[], storage=mock_storage)
        viewer.get_filter_suggestions("severity", "")
        viewer.get_filter_suggestions("agent_name", "")
        assert mock_storage.get_distinct_finding_values.call_count == 2

        # Repeated calls use cache
        viewer.get_filter_suggestions("severity", "")
        viewer.get_filter_suggestions("agent_name", "")
        assert mock_storage.get_distinct_finding_values.call_count == 2


class TestConfigEditorRenderCache:
    """Test config editor render caching."""

    def test_generation_bumps_on_state_change(self) -> None:
        viewer = FindingsViewer(rows=[_make_row(1), _make_row(2)])
        gen_before = viewer._render_generation
        viewer.move_down()
        assert viewer._render_generation > gen_before
