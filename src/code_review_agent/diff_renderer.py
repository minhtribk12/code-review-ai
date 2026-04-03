"""Delta-style diff rendering for findings detail panel.

Renders code snippets with syntax highlighting and diff coloring
using prompt_toolkit styled text tuples. Supports common languages.
"""

from __future__ import annotations

import re

# Language detection by file extension
_EXTENSION_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".sh": "bash",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".sql": "sql",
    ".html": "html",
    ".css": "css",
}

# Style constants for diff rendering
_STYLE_ADDED = "bg:ansigreen fg:ansiblack"
_STYLE_REMOVED = "bg:ansired fg:ansiwhite"
_STYLE_CONTEXT = ""
_STYLE_LINE_NUM = "dim"
_STYLE_HEADER = "bold cyan"

# Type alias for styled text fragments
_Lines = list[tuple[str, str]]


def detect_language(file_path: str | None) -> str:
    """Detect language from file extension."""
    if not file_path:
        return "text"
    ext = file_path[file_path.rfind(".") :] if "." in file_path else ""
    return _EXTENSION_TO_LANG.get(ext, "text")


def render_code_snippet(
    code: str,
    file_path: str | None = None,
    start_line: int = 1,
    highlight_lines: set[int] | None = None,
) -> _Lines:
    """Render a code snippet with line numbers and optional highlighting.

    Returns styled text tuples for prompt_toolkit rendering.
    """
    lines: _Lines = []
    highlight = highlight_lines or set()
    max_line = start_line + len(code.splitlines()) - 1
    gutter_width = len(str(max_line)) + 1

    for i, line in enumerate(code.splitlines(), start=start_line):
        line_num = f"{i:>{gutter_width}} "
        is_highlighted = i in highlight

        lines.append((_STYLE_LINE_NUM, f"   {line_num}"))
        if is_highlighted:
            lines.append((_STYLE_ADDED, f" {line}"))
        else:
            lines.append((_STYLE_CONTEXT, f" {line}"))
        lines.append(("", "\n"))

    return lines


def render_diff_snippet(
    patch: str,
    file_path: str | None = None,
    max_lines: int = 30,
) -> _Lines:
    """Render a unified diff patch with diff coloring.

    Added lines are green, removed lines are red, context is plain.
    Line numbers shown in the gutter.
    """
    lines: _Lines = []
    patch_lines = patch.splitlines()[:max_lines]
    old_line = 0
    new_line = 0

    for raw_line in patch_lines:
        # Hunk header
        hunk_match = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)", raw_line)
        if hunk_match:
            old_line = int(hunk_match.group(1))
            new_line = int(hunk_match.group(2))
            context = hunk_match.group(3)
            lines.append((_STYLE_HEADER, f"   @@ {raw_line} @@{context}\n"))
            continue

        if raw_line.startswith("+++") or raw_line.startswith("---"):
            lines.append((_STYLE_HEADER, f"   {raw_line}\n"))
            continue

        if raw_line.startswith("+"):
            gutter = f"   {new_line:>4}  "
            lines.append((_STYLE_LINE_NUM, gutter))
            lines.append((_STYLE_ADDED, f"+{raw_line[1:]}\n"))
            new_line += 1
        elif raw_line.startswith("-"):
            gutter = f"   {old_line:>4}  "
            lines.append((_STYLE_LINE_NUM, gutter))
            lines.append((_STYLE_REMOVED, f"-{raw_line[1:]}\n"))
            old_line += 1
        else:
            gutter = f"   {new_line:>4}  "
            lines.append((_STYLE_LINE_NUM, gutter))
            lines.append((_STYLE_CONTEXT, f" {raw_line}\n"))
            old_line += 1
            new_line += 1

    if len(patch.splitlines()) > max_lines:
        lines.append(("dim", f"   ... ({len(patch.splitlines()) - max_lines} more lines)\n"))

    return lines


def render_suggestion_as_diff(
    suggestion: str,
    file_path: str | None = None,
) -> _Lines:
    """Render a suggestion string, highlighting code blocks as diffs.

    Detects fenced code blocks (```...```) and renders them with
    syntax-aware coloring. Non-code text is rendered as-is.
    """
    lines: _Lines = []
    in_code_block = False
    code_lines: list[str] = []
    block_count = 0

    for line in suggestion.splitlines():
        if line.strip().startswith("```"):
            if in_code_block:
                # End of code block: render accumulated code
                block_count += 1
                style = _STYLE_ADDED if block_count % 2 == 0 else _STYLE_REMOVED
                if block_count == 1:
                    style = _STYLE_CONTEXT
                for cl in code_lines:
                    lines.append(("dim", "     "))
                    lines.append((style, f"{cl}\n"))
                code_lines = []
                in_code_block = False
            else:
                in_code_block = True
            continue

        if in_code_block:
            code_lines.append(line)
        else:
            lines.append(("", f"     {line}\n"))

    # Flush any unclosed code block
    for cl in code_lines:
        lines.append(("dim", "     "))
        lines.append((_STYLE_CONTEXT, f"{cl}\n"))

    return lines
