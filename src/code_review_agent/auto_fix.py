"""Auto-fix: parse suggestions and apply code patches from findings.

Each finding's ``suggestion`` field may contain a concrete code fix.
This module extracts the fix, generates a preview, and applies it
to the source file with backup for undo.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from pathlib import Path

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class FixResult:
    """Result of applying a fix."""

    is_applied: bool
    file_path: str
    message: str
    backup_path: str | None = None


@dataclass(frozen=True)
class ParsedFix:
    """A code fix extracted from a finding suggestion."""

    file_path: str
    line_number: int | None
    original_code: str | None
    replacement_code: str
    description: str


def parse_suggestion(
    suggestion: str | None,
    file_path: str | None,
    line_number: int | None,
) -> ParsedFix | None:
    """Parse a finding suggestion to extract a concrete code fix.

    Looks for code blocks in the suggestion text. If a code block is found
    with a "replace" or "change to" context, extracts both original and
    replacement. Otherwise treats the code block as the replacement.
    """
    if not suggestion or not file_path:
        return None

    # Look for fenced code blocks: ```lang\ncode\n```
    code_blocks = re.findall(r"```\w*\n(.*?)```", suggestion, re.DOTALL)
    if not code_blocks:
        # Try indented code blocks (4+ spaces)
        indented = re.findall(r"(?:^    .+\n?)+", suggestion, re.MULTILINE)
        if indented:
            code_blocks = ["\n".join(line[4:] for line in indented[0].splitlines())]

    if not code_blocks:
        return None

    # If two code blocks: first is original, second is replacement
    if len(code_blocks) >= 2:
        return ParsedFix(
            file_path=file_path,
            line_number=line_number,
            original_code=code_blocks[0].strip(),
            replacement_code=code_blocks[1].strip(),
            description=_extract_description(suggestion),
        )

    # Single code block: treat as replacement
    return ParsedFix(
        file_path=file_path,
        line_number=line_number,
        original_code=None,
        replacement_code=code_blocks[0].strip(),
        description=_extract_description(suggestion),
    )


def preview_fix(fix: ParsedFix) -> str:
    """Generate a human-readable preview of the fix."""
    lines: list[str] = []
    lines.append(f"File: {fix.file_path}")
    if fix.line_number:
        lines.append(f"Line: {fix.line_number}")
    lines.append(f"Fix: {fix.description}")
    if fix.original_code:
        lines.append("\n--- Original ---")
        lines.append(fix.original_code)
    lines.append("\n+++ Replacement +++")
    lines.append(fix.replacement_code)
    return "\n".join(lines)


def apply_fix(
    fix: ParsedFix,
    source_root: Path,
    *,
    create_backup: bool = True,
) -> FixResult:
    """Apply a fix to the source file.

    Creates a backup (.bak) before modifying. Returns a FixResult
    indicating success/failure with the backup path for undo.
    """
    file_path = source_root / fix.file_path
    if not file_path.is_file():
        return FixResult(
            is_applied=False,
            file_path=fix.file_path,
            message=f"File not found: {fix.file_path}",
        )

    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as exc:
        return FixResult(
            is_applied=False,
            file_path=fix.file_path,
            message=f"Failed to read file: {exc}",
        )

    # Create backup
    backup_path: str | None = None
    if create_backup:
        bak = file_path.with_suffix(file_path.suffix + ".bak")
        try:
            shutil.copy2(str(file_path), str(bak))
            backup_path = str(bak)
        except Exception as exc:
            return FixResult(
                is_applied=False,
                file_path=fix.file_path,
                message=f"Failed to create backup: {exc}",
            )

    # Apply the fix
    if fix.original_code and fix.original_code in content:
        new_content = content.replace(fix.original_code, fix.replacement_code, 1)
    elif fix.line_number is not None:
        lines = content.splitlines(keepends=True)
        idx = fix.line_number - 1
        if 0 <= idx < len(lines):
            lines[idx] = fix.replacement_code + "\n"
            new_content = "".join(lines)
        else:
            return FixResult(
                is_applied=False,
                file_path=fix.file_path,
                message=f"Line {fix.line_number} out of range",
                backup_path=backup_path,
            )
    else:
        return FixResult(
            is_applied=False,
            file_path=fix.file_path,
            message="Cannot determine where to apply fix (no original code or line number)",
            backup_path=backup_path,
        )

    try:
        file_path.write_text(new_content, encoding="utf-8")
    except Exception as exc:
        return FixResult(
            is_applied=False,
            file_path=fix.file_path,
            message=f"Failed to write file: {exc}",
            backup_path=backup_path,
        )

    logger.info(f"applied fix to {fix.file_path}:{fix.line_number or '?'}")
    return FixResult(
        is_applied=True,
        file_path=fix.file_path,
        message="Fix applied successfully",
        backup_path=backup_path,
    )


def undo_fix(backup_path: str, original_path: Path) -> bool:
    """Restore a file from its backup. Returns True on success."""
    try:
        shutil.copy2(backup_path, str(original_path))
        return True
    except Exception:
        logger.debug(f"failed to undo fix from {backup_path}", exc_info=True)
        return False


def _extract_description(suggestion: str) -> str:
    """Extract a one-line description from the suggestion text."""
    # Take the first non-empty, non-code line
    for line in suggestion.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("```") and not stripped.startswith("    "):
            return stripped[:100]
    return "Apply suggested fix"
