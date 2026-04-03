"""Diff-aware context enrichment for agent prompts.

Parses unified diffs to identify changed functions/classes, then fetches
surrounding context (function body, class definition, imports) from the
actual source file. Enriched context is included in agent prompts with
clear markers: [CHANGED] vs [CONTEXT].
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from pathlib import Path

logger = structlog.get_logger(__name__)

# Languages supported for AST-based context extraction
_PYTHON_EXTENSIONS = frozenset({".py"})

# Regex for detecting function/class definitions across languages
_FUNCTION_PATTERNS: dict[str, re.Pattern[str]] = {
    ".py": re.compile(r"^\s*(async\s+)?def\s+(\w+)\s*\("),
    ".js": re.compile(r"^\s*(async\s+)?function\s+(\w+)\s*\(|^\s*(const|let|var)\s+(\w+)\s*=.*=>"),
    ".ts": re.compile(r"^\s*(async\s+)?function\s+(\w+)\s*\(|^\s*(const|let|var)\s+(\w+)\s*=.*=>"),
    ".java": re.compile(r"^\s*(public|private|protected)?\s*(static\s+)?\w+\s+(\w+)\s*\("),
    ".go": re.compile(r"^func\s+(\(.*?\)\s*)?(\w+)\s*\("),
    ".rs": re.compile(r"^\s*(pub\s+)?(async\s+)?fn\s+(\w+)"),
}

_CLASS_PATTERNS: dict[str, re.Pattern[str]] = {
    ".py": re.compile(r"^\s*class\s+(\w+)"),
    ".js": re.compile(r"^\s*class\s+(\w+)"),
    ".ts": re.compile(r"^\s*(export\s+)?class\s+(\w+)"),
    ".java": re.compile(r"^\s*(public|private)?\s*class\s+(\w+)"),
    ".go": re.compile(r"^type\s+(\w+)\s+struct"),
    ".rs": re.compile(r"^\s*(pub\s+)?struct\s+(\w+)"),
}


@dataclass(frozen=True)
class HunkContext:
    """Context for a single diff hunk."""

    file_path: str
    changed_lines: tuple[int, ...]
    enclosing_function: str | None
    enclosing_class: str | None
    context_before: str  # lines before the hunk for context
    context_after: str  # lines after the hunk for context


@dataclass(frozen=True)
class EnrichedDiff:
    """A diff file enriched with surrounding context."""

    filename: str
    original_patch: str
    hunks: tuple[HunkContext, ...]
    imports_section: str  # import statements from the file


@dataclass
class DiffHunk:
    """Parsed hunk from unified diff."""

    start_line: int
    line_count: int
    changed_line_numbers: list[int] = field(default_factory=list)


def parse_diff_hunks(patch: str) -> list[DiffHunk]:
    """Parse unified diff format to extract hunk ranges and changed lines."""
    hunks: list[DiffHunk] = []
    current_line = 0

    for line in patch.splitlines():
        hunk_match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
        if hunk_match:
            start = int(hunk_match.group(1))
            count = int(hunk_match.group(2) or "1")
            hunks.append(DiffHunk(start_line=start, line_count=count))
            current_line = start - 1
            continue

        if not hunks:
            continue

        if line.startswith("+") and not line.startswith("+++"):
            current_line += 1
            hunks[-1].changed_line_numbers.append(current_line)
        elif line.startswith("-"):
            pass  # removed lines don't affect new file line numbers
        else:
            current_line += 1

    return hunks


def find_enclosing_scope(
    lines: list[str],
    target_line: int,
    file_ext: str,
) -> tuple[str | None, str | None]:
    """Find the function and class enclosing a given line number.

    Returns (function_name, class_name).
    """
    func_pattern = _FUNCTION_PATTERNS.get(file_ext)
    class_pattern = _CLASS_PATTERNS.get(file_ext)

    enclosing_function: str | None = None
    enclosing_class: str | None = None

    for i in range(min(target_line - 1, len(lines) - 1), -1, -1):
        line = lines[i]

        if func_pattern and enclosing_function is None:
            match = func_pattern.search(line)
            if match:
                # Extract the function name: skip keyword groups
                enclosing_function = _extract_identifier(match)

        if class_pattern and enclosing_class is None:
            match = class_pattern.search(line)
            if match:
                enclosing_class = _extract_identifier(match)

        if enclosing_function and enclosing_class:
            break

    return enclosing_function, enclosing_class


_KEYWORD_GROUPS = frozenset(
    {
        "async",
        "async ",
        "const",
        "let",
        "var",
        "pub",
        "pub ",
        "public",
        "private",
        "protected",
        "static",
        "static ",
        "export",
        "export ",
    }
)


def _extract_identifier(match: re.Match[str]) -> str | None:
    """Extract the identifier (non-keyword) group from a regex match."""
    for g in match.groups():
        if g and g.strip() not in _KEYWORD_GROUPS and g.strip().isidentifier():
            return g.strip()
    return None


def extract_imports(lines: list[str], file_ext: str) -> str:
    """Extract import statements from the beginning of a file."""
    import_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("//"):
            continue
        if (
            (
                file_ext == ".py"
                and (stripped.startswith("import ") or stripped.startswith("from "))
            )
            or (file_ext in (".js", ".ts") and stripped.startswith("import "))
            or (file_ext == ".java" and stripped.startswith("import "))
            or (file_ext == ".go" and stripped.startswith("import"))
            or (file_ext == ".rs" and stripped.startswith("use "))
        ):
            import_lines.append(line.rstrip())
        elif import_lines and not _is_import_line(stripped, file_ext):
            # Stop after first non-import line (past the header)
            break
    return "\n".join(import_lines)


def _is_import_line(line: str, file_ext: str) -> bool:
    """Check if a line is an import statement."""
    if file_ext == ".py":
        return line.startswith(("import ", "from "))
    if file_ext in (".js", ".ts"):
        return line.startswith("import ")
    if file_ext == ".java":
        return line.startswith("import ")
    return False


def get_context_window(
    lines: list[str],
    start_line: int,
    end_line: int,
    context_lines: int = 5,
) -> tuple[str, str]:
    """Get context before and after a range of lines.

    Returns (context_before, context_after) as strings.
    """
    before_start = max(0, start_line - 1 - context_lines)
    before_end = max(0, start_line - 1)
    after_start = min(len(lines), end_line)
    after_end = min(len(lines), end_line + context_lines)

    before = "\n".join(lines[before_start:before_end])
    after = "\n".join(lines[after_start:after_end])
    return before, after


def enrich_diff_file(
    filename: str,
    patch: str,
    source_root: Path | None = None,
    context_lines: int = 5,
) -> EnrichedDiff:
    """Enrich a single diff file with surrounding context.

    If ``source_root`` is provided and the file exists, reads the full
    file for context. Otherwise returns the diff with parsed hunk info only.
    """
    ext = _get_extension(filename)
    hunks = parse_diff_hunks(patch)

    file_lines: list[str] = []
    if source_root is not None:
        file_path = source_root / filename
        if file_path.is_file():
            try:
                file_lines = file_path.read_text(encoding="utf-8").splitlines()
            except Exception:
                logger.debug(f"failed to read source file {file_path}")

    imports = extract_imports(file_lines, ext) if file_lines else ""

    enriched_hunks: list[HunkContext] = []
    for hunk in hunks:
        if not hunk.changed_line_numbers:
            continue

        func_name, class_name = (None, None)
        if file_lines:
            func_name, class_name = find_enclosing_scope(
                file_lines, hunk.changed_line_numbers[0], ext
            )

        before, after = ("", "")
        if file_lines:
            before, after = get_context_window(
                file_lines,
                hunk.start_line,
                hunk.start_line + hunk.line_count,
                context_lines=context_lines,
            )

        enriched_hunks.append(
            HunkContext(
                file_path=filename,
                changed_lines=tuple(hunk.changed_line_numbers),
                enclosing_function=func_name,
                enclosing_class=class_name,
                context_before=before,
                context_after=after,
            )
        )

    return EnrichedDiff(
        filename=filename,
        original_patch=patch,
        hunks=tuple(enriched_hunks),
        imports_section=imports,
    )


def format_enriched_context(enriched: EnrichedDiff) -> str:
    """Format enriched diff for inclusion in agent prompts.

    Adds [CONTEXT] markers around surrounding code and [CHANGED] markers
    around the actual diff hunks.
    """
    parts: list[str] = []

    if enriched.imports_section:
        parts.append("[CONTEXT: imports]")
        parts.append(enriched.imports_section)
        parts.append("[/CONTEXT]")

    for hunk in enriched.hunks:
        scope_info: list[str] = []
        if hunk.enclosing_class:
            scope_info.append(f"class {hunk.enclosing_class}")
        if hunk.enclosing_function:
            scope_info.append(f"function {hunk.enclosing_function}")

        if scope_info:
            parts.append(f"[SCOPE: {' > '.join(scope_info)}]")

        if hunk.context_before:
            parts.append("[CONTEXT: before]")
            parts.append(hunk.context_before)
            parts.append("[/CONTEXT]")

        parts.append(f"[CHANGED: lines {hunk.changed_lines[0]}-{hunk.changed_lines[-1]}]")

        if hunk.context_after:
            parts.append("[CONTEXT: after]")
            parts.append(hunk.context_after)
            parts.append("[/CONTEXT]")

    return "\n".join(parts)


def _get_extension(filename: str) -> str:
    """Extract file extension."""
    dot = filename.rfind(".")
    if dot == -1:
        return ""
    return filename[dot:]
