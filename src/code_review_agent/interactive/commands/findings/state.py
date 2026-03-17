"""Core state machine for the findings navigator.

Manages cursor position, mode transitions, sort/filter state, horizontal
scroll, and triage state. Pure state logic -- no prompt_toolkit imports.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import structlog

from .models import (
    _ALL_COLUMNS,
    _COLUMN_DEFS,
    _DEFAULT_VISIBLE,
    _SEVERITY_ORDER,
    ActiveFilter,
    ConfirmAction,
    FindingRow,
    TriageAction,
    ViewerMode,
)

if TYPE_CHECKING:
    from code_review_agent.models import ReviewReport
    from code_review_agent.storage import ReviewStorage

logger = structlog.get_logger(__name__)

_SETTINGS_KEY_COLUMNS = "visible_columns"


class FindingsViewer:
    """State machine for the findings navigator TUI."""

    def __init__(
        self,
        *,
        rows: list[FindingRow] | None = None,
        report: ReviewReport | None = None,
        github_token: str | None = None,
        storage: ReviewStorage | None = None,
    ) -> None:
        if rows is not None:
            self.all_rows: list[FindingRow] = rows
        elif report is not None:
            from code_review_agent.interactive.commands.findings_cmd import (
                _flatten_findings,
            )

            self.all_rows = _flatten_findings(report)
        else:
            self.all_rows = []
        self.visible_rows: list[FindingRow] = []
        self.cursor: int = 0
        self.mode: ViewerMode = ViewerMode.NAVIGATE
        self.h_offset: int = 0
        self.is_detail_open: bool = False
        self.status_message: str = ""

        self.sort_columns: list[str] = [
            "severity",
            "agent_name",
            "file_path",
            "title",
        ]
        self.sort_index: int = 0
        self.is_sort_reversed: bool = False

        self.triage: dict[int | None, TriageAction] = {}
        self.posted_indices: set[int | None] = set()
        self.staged_for_pr: set[int | None] = set()
        self.active_filters: list[ActiveFilter] = []

        self.pending_confirm: ConfirmAction | None = None

        # Filter UI state
        self.filter_cursor: int = 0
        self.filter_input: str = ""
        self.filter_suggestions: list[str] = []
        self.filter_dimension: str = "severity"
        self.filter_dimension_index: int = 0

        self.report: ReviewReport | None = report
        self.github_token: str | None = github_token
        self._storage: ReviewStorage | None = storage

        self.comments_posted: int = 0
        self.comments_deleted: int = 0
        self.last_review_id: int | None = None
        self.last_comment_ids: list[int] = []

        # Load triage and posted state from row data (keyed by finding_db_id)
        for row in self.all_rows:
            if row.finding_db_id is None:
                continue
            try:
                action = TriageAction(row.triage_action)
            except ValueError:
                action = TriageAction.OPEN
            self.triage[row.finding_db_id] = action
            if row.is_posted:
                self.posted_indices.add(row.finding_db_id)

        # Load visible columns from storage
        self.visible_columns: list[str] = self._load_visible_columns()

        # Apply initial filters (hide solved by default)
        self._apply_filters()

    # -- Column persistence ------------------------------------------------

    def _load_visible_columns(self) -> list[str]:
        if self._storage is None:
            return list(_DEFAULT_VISIBLE)
        raw = self._storage.load_finding_setting(_SETTINGS_KEY_COLUMNS)
        if raw is None:
            return list(_DEFAULT_VISIBLE)
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list) and all(isinstance(c, str) for c in parsed):
                valid_keys = {key for key, _, _ in _ALL_COLUMNS}
                return [c for c in parsed if c in valid_keys] or list(_DEFAULT_VISIBLE)
        except (json.JSONDecodeError, TypeError):
            pass
        return list(_DEFAULT_VISIBLE)

    def _persist_visible_columns(self) -> None:
        if self._storage is None:
            return
        self._storage.save_finding_setting(
            _SETTINGS_KEY_COLUMNS,
            json.dumps(self.visible_columns),
        )

    # -- Navigation --------------------------------------------------------

    def move_up(self) -> None:
        if self.visible_rows and self.cursor > 0:
            self.cursor -= 1

    def move_down(self) -> None:
        if self.visible_rows and self.cursor < len(self.visible_rows) - 1:
            self.cursor += 1

    def scroll_left(self) -> None:
        self.h_offset = max(0, self.h_offset - 4)

    def scroll_right(self) -> None:
        self.h_offset = min(self._max_h_offset(), self.h_offset + 4)

    def _max_h_offset(self) -> int:
        import shutil

        term_width = shutil.get_terminal_size((120, 40)).columns
        col_meta = self._compute_column_widths(self.visible_columns, term_width)
        total_col_width = sum(w for _, _, w in col_meta)
        return max(0, total_col_width - (term_width - 3))

    def open_detail(self) -> None:
        self.mode = ViewerMode.DETAIL
        self.is_detail_open = True

    def close_detail(self) -> None:
        self.mode = ViewerMode.NAVIGATE
        self.is_detail_open = False

    # -- Sort --------------------------------------------------------------

    def cycle_sort(self) -> None:
        if self.sort_index == len(self.sort_columns) - 1:
            self.sort_index = 0
            self.is_sort_reversed = not self.is_sort_reversed
        else:
            self.sort_index += 1
        self._apply_sort()

    def _apply_sort(self) -> None:
        col = self.sort_columns[self.sort_index]

        def sort_key(row: FindingRow) -> tuple[int, str]:
            if col == "severity":
                try:
                    order = _SEVERITY_ORDER.index(row.severity)
                except ValueError:
                    order = len(_SEVERITY_ORDER)
                return (order, "")
            val = getattr(row, col, "") or ""
            return (0, str(val).lower())

        self.visible_rows.sort(
            key=sort_key,
            reverse=self.is_sort_reversed,
        )
        self.cursor = min(self.cursor, max(0, len(self.visible_rows) - 1))

    # -- Filters -----------------------------------------------------------

    def add_filter(self, field: str, value: str) -> None:
        for i, f in enumerate(self.active_filters):
            if f.field == field:
                merged = ActiveFilter(field=field, values=f.values | {value})
                self.active_filters[i] = merged
                self._apply_filters()
                return
        self.active_filters.append(ActiveFilter(field=field, values={value}))
        self._apply_filters()

    def remove_filter(self, index: int) -> None:
        if 0 <= index < len(self.active_filters):
            self.active_filters.pop(index)
            self._apply_filters()

    def clear_filters(self) -> None:
        self.active_filters.clear()
        self._apply_filters()

    def _apply_filters(self) -> None:
        """Rebuild visible_rows from all_rows based on active filters.

        Triage filter semantics:
        - No triage filter: show all except solved (default)
        - Triage filter present: show ONLY the selected triage states
          (exclusive -- replaces the default set)

        Other filters use AND logic: a row must match ALL active filters.
        """
        # Build the set of visible triage states
        has_triage_filter = False
        triage_values: set[str] = set()
        for af in self.active_filters:
            if af.field == "triage_action":
                has_triage_filter = True
                triage_values |= af.values

        if has_triage_filter:
            visible_triage = triage_values
        else:
            # Default: show everything except solved
            visible_triage = {"open", "false_positive", "ignored"}

        rows: list[FindingRow] = []
        for r in self.all_rows:
            # Check triage visibility
            current_triage = str(self.triage.get(r.finding_db_id, TriageAction.OPEN))
            if current_triage not in visible_triage:
                continue

            # Check non-triage filters (AND logic)
            is_visible = True
            for af in self.active_filters:
                if af.field == "triage_action":
                    continue  # already handled above
                if not self._row_matches_filter(r, af):
                    is_visible = False
                    break
            if is_visible:
                rows.append(r)

        self.visible_rows = rows
        self._apply_sort()

    def _row_matches_filter(self, row: FindingRow, af: ActiveFilter) -> bool:
        val = getattr(row, af.field, None)
        return str(val or "").lower() in {v.lower() for v in af.values}

    def open_filter(self) -> None:
        """Enter filter mode."""
        from .filters import FILTER_DIMENSIONS

        self.mode = ViewerMode.FILTER
        self.filter_cursor = 0
        self.filter_input = ""
        self.filter_suggestions = []
        self.filter_dimension_index = 0
        self.filter_dimension = FILTER_DIMENSIONS[0][0]
        self.update_filter_suggestions()

    def cycle_filter_dimension(self, direction: int) -> None:
        """Cycle the filter dimension left or right."""
        from .filters import FILTER_DIMENSIONS

        n = len(FILTER_DIMENSIONS)
        self.filter_dimension_index = (self.filter_dimension_index + direction) % n
        self.filter_dimension = FILTER_DIMENSIONS[self.filter_dimension_index][0]
        self.filter_input = ""
        self.update_filter_suggestions()

    def cancel_filter(self) -> None:
        """Exit filter mode without applying."""
        self.mode = ViewerMode.NAVIGATE

    def apply_filters(self) -> None:
        """Apply current filters and return to navigate mode."""
        self._apply_filters()
        self.mode = ViewerMode.NAVIGATE

    def select_filter_suggestion(self) -> None:
        """Add the currently selected suggestion as a filter."""
        if not self.filter_suggestions:
            return
        idx = max(0, min(self.filter_cursor, len(self.filter_suggestions) - 1))
        value = self.filter_suggestions[idx]
        self.add_filter(self.filter_dimension, value)
        self.filter_input = ""
        self.update_filter_suggestions()
        self.status_message = f"Filter added: {self.filter_dimension}={value}"

    def update_filter_suggestions(self) -> None:
        """Refresh autocomplete suggestions based on current input."""
        self.filter_suggestions = self.get_filter_suggestions(
            self.filter_dimension,
            self.filter_input,
        )
        self.filter_cursor = 0

    def remove_current_filter(self) -> None:
        """Remove the filter at the current cursor position."""
        if self.active_filters:
            idx = min(self.filter_cursor, len(self.active_filters) - 1)
            self.remove_filter(idx)
            self.filter_cursor = min(
                self.filter_cursor,
                max(0, len(self.active_filters) - 1),
            )

    def get_filter_suggestions(self, field: str, prefix: str) -> list[str]:
        if self._storage is None:
            return []
        values = self._storage.get_distinct_finding_values(field)
        if not prefix:
            return values
        lower_prefix = prefix.lower()
        return [v for v in values if v.lower().startswith(lower_prefix)]

    # -- Triage ------------------------------------------------------------

    def toggle_triage(self, action: TriageAction) -> None:
        if not self.visible_rows:
            return
        row = self.visible_rows[self.cursor]
        db_id = row.finding_db_id
        if db_id is None:
            return
        current = self.triage.get(db_id, TriageAction.OPEN)
        new_action = TriageAction.OPEN if current == action else action
        self.triage[db_id] = new_action
        self._persist_triage(row, new_action)
        self._apply_filters()
        self.status_message = f"{row.title} -> {new_action.value}"
        logger.debug(
            "triage toggled",
            finding_index=row.index,
            old=current.value,
            new=new_action.value,
        )

    def _persist_triage(self, row: FindingRow, action: TriageAction) -> None:
        if self._storage is None or row.finding_db_id is None:
            return
        self._storage.update_finding_triage(row.finding_db_id, action.value)

    # -- Confirm -----------------------------------------------------------

    def request_confirm(self, action: str, description: str) -> None:
        if not self.visible_rows:
            return
        row = self.visible_rows[self.cursor]
        self.pending_confirm = ConfirmAction(
            action=action,
            description=description,
            finding_row=row,
        )
        self.mode = ViewerMode.CONFIRM

    def confirm_yes(self) -> ConfirmAction | None:
        """Execute pending confirmation. Returns the action for the caller."""
        result = self.pending_confirm
        self.pending_confirm = None
        self.mode = ViewerMode.DETAIL if self.is_detail_open else ViewerMode.NAVIGATE
        return result

    def confirm_no(self) -> None:
        self.pending_confirm = None
        self.mode = ViewerMode.DETAIL if self.is_detail_open else ViewerMode.NAVIGATE

    # -- PR resolution -----------------------------------------------------

    def _resolve_pr_url(self) -> str | None:
        if self.report and self.report.pr_url:
            return self.report.pr_url
        if not self.visible_rows:
            return None
        row = self.visible_rows[self.cursor]
        if row.repo and row.pr_number:
            return f"https://github.com/{row.repo}/pull/{row.pr_number}"
        return None

    # -- Static helpers ----------------------------------------------------

    @staticmethod
    def _slice_styled_line(
        fragments: list[tuple[str, str]],
        start: int,
        length: int,
    ) -> list[tuple[str, str]]:
        """Slice a list of (style, text) fragments by character offset.

        Returns fragments covering characters [start, start+length).
        """
        result: list[tuple[str, str]] = []
        pos = 0
        remaining = length
        for style, text in fragments:
            seg_len = len(text)
            if pos + seg_len <= start:
                pos += seg_len
                continue
            clip_start = max(0, start - pos)
            clip_end = min(seg_len, clip_start + remaining)
            chunk = text[clip_start:clip_end]
            if chunk:
                result.append((style, chunk))
                remaining -= len(chunk)
            pos += seg_len
            if remaining <= 0:
                break
        return result

    @staticmethod
    def _compute_column_widths(
        visible_columns: list[str],
        term_width: int,
    ) -> list[tuple[str, str, int]]:
        """Compute column widths proportional to terminal width.

        Returns list of (key, label, width) for each visible column.
        """
        defs_map = {key: (label, weight, min_w) for key, label, weight, min_w in _COLUMN_DEFS}
        active = [(key, *defs_map[key]) for key in visible_columns if key in defs_map]
        if not active:
            return []

        total_weight = sum(weight for _, _, weight, _ in active)
        usable = max(term_width - len(active), 20)

        result: list[tuple[str, str, int]] = []
        for key, label, weight, min_w in active:
            width = max(min_w, int(usable * weight / total_weight))
            result.append((key, label, width))

        return result
