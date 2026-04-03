"""Tests for watch mode incremental review."""

from __future__ import annotations

from code_review_agent.models import DiffFile, DiffStatus
from code_review_agent.watch_incremental import (
    WatchState,
    format_delta_summary,
)


def _make_diff(filename: str, patch: str = "content") -> DiffFile:
    return DiffFile(filename=filename, patch=patch, status=DiffStatus.MODIFIED)


class TestWatchState:
    def test_first_review_all_new(self) -> None:
        state = WatchState()
        files = [_make_diff("a.py"), _make_diff("b.py")]
        delta = state.compute_delta(files)
        assert len(delta.new_files) == 2
        assert len(delta.modified_files) == 0
        assert delta.has_changes

    def test_unchanged_files(self) -> None:
        state = WatchState()
        files = [_make_diff("a.py", "v1")]
        state.update_state(files)
        delta = state.compute_delta(files)
        assert len(delta.unchanged_files) == 1
        assert len(delta.new_files) == 0
        assert not delta.has_changes

    def test_modified_file(self) -> None:
        state = WatchState()
        files_v1 = [_make_diff("a.py", "version1")]
        state.update_state(files_v1)
        files_v2 = [_make_diff("a.py", "version2")]
        delta = state.compute_delta(files_v2)
        assert len(delta.modified_files) == 1
        assert delta.has_changes

    def test_mixed_changes(self) -> None:
        state = WatchState()
        state.update_state([_make_diff("a.py", "v1"), _make_diff("b.py", "v1")])
        delta = state.compute_delta(
            [
                _make_diff("a.py", "v1"),  # unchanged
                _make_diff("b.py", "v2"),  # modified
                _make_diff("c.py", "new"),  # new
            ]
        )
        assert len(delta.unchanged_files) == 1
        assert len(delta.modified_files) == 1
        assert len(delta.new_files) == 1

    def test_filter_changed_files(self) -> None:
        state = WatchState()
        state.update_state([_make_diff("a.py", "v1")])
        files = [_make_diff("a.py", "v1"), _make_diff("b.py", "new")]
        delta = state.compute_delta(files)
        changed = state.filter_changed_files(files, delta)
        assert len(changed) == 1
        assert changed[0].filename == "b.py"

    def test_clear(self) -> None:
        state = WatchState()
        state.update_state([_make_diff("a.py")])
        assert state.tracked_file_count == 1
        state.clear()
        assert state.tracked_file_count == 0
        assert state.review_count == 0

    def test_review_count(self) -> None:
        state = WatchState()
        state.update_state([_make_diff("a.py")])
        state.update_state([_make_diff("a.py")])
        assert state.review_count == 2


class TestFormatDeltaSummary:
    def test_all_new(self) -> None:
        state = WatchState()
        delta = state.compute_delta([_make_diff("a.py")])
        summary = format_delta_summary(delta)
        assert "1 new" in summary
        assert "+ a.py" in summary

    def test_change_summary(self) -> None:
        state = WatchState()
        delta = state.compute_delta([_make_diff("a.py")])
        assert "1 new" in delta.change_summary
