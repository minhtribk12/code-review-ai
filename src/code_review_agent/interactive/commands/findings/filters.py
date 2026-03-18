"""Filter logic and autocomplete for the findings navigator."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from .models import ActiveFilter, FindingRow, TriageAction

if TYPE_CHECKING:
    from code_review_agent.storage import ReviewStorage

logger = structlog.get_logger(__name__)

# Fields that can be filtered with their display labels
FILTER_DIMENSIONS: list[tuple[str, str]] = [
    ("severity", "Severity"),
    ("agent_name", "Agent"),
    ("repo", "Repository"),
    ("file_path", "File"),
    ("category", "Category"),
    ("triage_action", "Status"),
]


def get_suggestions(
    storage: ReviewStorage | None,
    field: str,
    prefix: str,
) -> list[str]:
    """Get autocomplete suggestions for a filter field.

    Queries distinct values from the database, filtered by prefix.
    """
    if storage is None:
        return []
    try:
        all_values = storage.get_distinct_finding_values(field)
        if not prefix:
            return all_values
        prefix_lower = prefix.lower()
        return [v for v in all_values if prefix_lower in v.lower()]
    except Exception:
        logger.debug("failed to get filter suggestions", exc_info=True)
        return []


def apply_filters(
    all_rows: list[FindingRow],
    active_filters: list[ActiveFilter],
    triage_state: dict[int, TriageAction],
    *,
    show_solved: bool = False,
) -> list[FindingRow]:
    """Apply active filters to the full row list.

    Filters are AND-combined: a row must match ALL active filters.
    Solved findings are hidden by default unless show_solved is True.
    """
    result: list[FindingRow] = []

    for row in all_rows:
        # Hide solved by default
        row_triage = triage_state.get(row.index, TriageAction.OPEN)
        if not show_solved and row_triage == TriageAction.SOLVED:
            continue

        # Check all active filters (AND logic)
        if not _matches_all_filters(row, active_filters):
            continue

        result.append(row)

    return result


def _matches_all_filters(
    row: FindingRow,
    filters: list[ActiveFilter],
) -> bool:
    """Check if a row matches all active filters."""
    for f in filters:
        row_value = _get_field_value(row, f.field)
        if row_value is None:
            return False
        if row_value not in f.values:
            return False
    return True


def _get_field_value(row: FindingRow, field: str) -> str | None:
    """Extract a field value from a FindingRow for filtering."""
    if field == "severity":
        return row.severity.value
    if field == "agent_name":
        return row.agent_name
    if field == "repo":
        return row.repo
    if field == "file_path":
        return row.file_path
    if field == "category":
        return row.category
    if field == "triage_action":
        return row.triage_action
    return None
