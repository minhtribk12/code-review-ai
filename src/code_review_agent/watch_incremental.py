"""Watch mode with incremental review.

Tracks file hashes to detect changes since last review. Only re-reviews
changed hunks, showing a before/after comparison of findings.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from code_review_agent.models import DiffFile

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class FileState:
    """Tracked state of a file from the last review."""

    file_path: str
    content_hash: str


@dataclass(frozen=True)
class IncrementalDelta:
    """Delta between two review states."""

    new_files: tuple[str, ...]
    modified_files: tuple[str, ...]
    unchanged_files: tuple[str, ...]

    @property
    def has_changes(self) -> bool:
        return bool(self.new_files or self.modified_files)

    @property
    def change_summary(self) -> str:
        parts: list[str] = []
        if self.new_files:
            parts.append(f"{len(self.new_files)} new")
        if self.modified_files:
            parts.append(f"{len(self.modified_files)} modified")
        if self.unchanged_files:
            parts.append(f"{len(self.unchanged_files)} unchanged")
        return ", ".join(parts) if parts else "no changes"


@dataclass
class WatchState:
    """Tracks file states across review cycles for incremental review."""

    _file_states: dict[str, str] = field(default_factory=dict)
    _review_count: int = 0

    def compute_delta(self, diff_files: list[DiffFile]) -> IncrementalDelta:
        """Compare current diff files against the last reviewed state."""
        new_files: list[str] = []
        modified_files: list[str] = []
        unchanged_files: list[str] = []

        for df in diff_files:
            current_hash = _hash_content(df.patch)
            previous_hash = self._file_states.get(df.filename)

            if previous_hash is None:
                new_files.append(df.filename)
            elif current_hash != previous_hash:
                modified_files.append(df.filename)
            else:
                unchanged_files.append(df.filename)

        return IncrementalDelta(
            new_files=tuple(new_files),
            modified_files=tuple(modified_files),
            unchanged_files=tuple(unchanged_files),
        )

    def update_state(self, diff_files: list[DiffFile]) -> None:
        """Update tracked file states after a review."""
        for df in diff_files:
            self._file_states[df.filename] = _hash_content(df.patch)
        self._review_count += 1

    def filter_changed_files(
        self,
        diff_files: list[DiffFile],
        delta: IncrementalDelta,
    ) -> list[DiffFile]:
        """Filter diff files to only include new and modified ones."""
        changed = set(delta.new_files) | set(delta.modified_files)
        return [df for df in diff_files if df.filename in changed]

    @property
    def review_count(self) -> int:
        return self._review_count

    @property
    def tracked_file_count(self) -> int:
        return len(self._file_states)

    def clear(self) -> None:
        """Reset all tracked state."""
        self._file_states.clear()
        self._review_count = 0


def format_delta_summary(delta: IncrementalDelta) -> str:
    """Format a delta summary for display."""
    lines: list[str] = [f"  Changes: {delta.change_summary}"]

    if delta.new_files:
        lines.append("  New files:")
        for f in delta.new_files:
            lines.append(f"    + {f}")

    if delta.modified_files:
        lines.append("  Modified files:")
        for f in delta.modified_files:
            lines.append(f"    ~ {f}")

    if delta.unchanged_files:
        lines.append(f"  Unchanged: {len(delta.unchanged_files)} file(s)")

    return "\n".join(lines)


def _hash_content(content: str) -> str:
    """SHA-256 hash of content for change detection."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
