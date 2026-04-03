"""Fuzzy search across all finding fields.

Simple substring matching with scoring. No external dependencies.
Invoked with / in the findings navigator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from code_review_agent.interactive.commands.findings.models import FindingRow


@dataclass(frozen=True)
class SearchResult:
    """A finding row with its match score."""

    row: FindingRow
    score: float  # 0.0 to 1.0
    matched_field: str


def fuzzy_search(
    rows: list[FindingRow],
    query: str,
) -> list[SearchResult]:
    """Search all visible fields of finding rows against a query.

    Returns results sorted by match score (highest first).
    Uses case-insensitive substring matching with field-weight scoring.
    """
    if not query or not rows:
        return []

    query_lower = query.lower()
    results: list[SearchResult] = []

    for row in rows:
        best_score = 0.0
        best_field = ""

        for field_name, value, weight in _searchable_fields(row):
            if not value:
                continue
            score = _score_match(query_lower, value.lower(), weight)
            if score > best_score:
                best_score = score
                best_field = field_name

        if best_score > 0.0:
            results.append(SearchResult(row=row, score=best_score, matched_field=best_field))

    return sorted(results, key=lambda r: r.score, reverse=True)


def _searchable_fields(row: FindingRow) -> list[tuple[str, str, float]]:
    """Return (field_name, value, weight) tuples for searchable fields."""
    return [
        ("title", row.title, 1.0),
        ("file_path", row.file_path or "", 0.9),
        ("description", row.description, 0.7),
        ("agent_name", row.agent_name, 0.6),
        ("category", row.category, 0.5),
        ("suggestion", row.suggestion or "", 0.4),
        ("severity", row.severity.value, 0.3),
    ]


def _score_match(query: str, text: str, weight: float) -> float:
    """Score a query against text. Returns 0.0 if no match.

    Scoring:
    - Exact match: weight * 1.0
    - Starts with: weight * 0.8
    - Contains: weight * 0.6
    - Word boundary match: weight * 0.7
    """
    if not query or not text:
        return 0.0

    if query == text:
        return weight * 1.0

    if text.startswith(query):
        return weight * 0.8

    # Check word boundary match
    words = text.split()
    for word in words:
        if word.startswith(query):
            return weight * 0.7

    if query in text:
        return weight * 0.6

    return 0.0
