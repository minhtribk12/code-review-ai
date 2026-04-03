"""Tests for delta-style diff rendering."""

from __future__ import annotations

from code_review_agent.diff_renderer import (
    detect_language,
    render_code_snippet,
    render_diff_snippet,
    render_suggestion_as_diff,
)


class TestDetectLanguage:
    def test_python(self) -> None:
        assert detect_language("app.py") == "python"

    def test_javascript(self) -> None:
        assert detect_language("app.js") == "javascript"

    def test_typescript(self) -> None:
        assert detect_language("app.ts") == "typescript"

    def test_go(self) -> None:
        assert detect_language("main.go") == "go"

    def test_unknown(self) -> None:
        assert detect_language("Makefile") == "text"

    def test_none(self) -> None:
        assert detect_language(None) == "text"


class TestRenderCodeSnippet:
    def test_basic_rendering(self) -> None:
        code = "x = 1\ny = 2\nz = 3"
        lines = render_code_snippet(code, start_line=10)
        text = "".join(t for _, t in lines)
        assert "10" in text
        assert "x = 1" in text
        assert "12" in text

    def test_highlight_lines(self) -> None:
        code = "a = 1\nb = 2"
        lines = render_code_snippet(code, start_line=1, highlight_lines={2})
        # Line 2 should have the added style
        styles = [s for s, t in lines if "b = 2" in t]
        assert any("green" in s for s in styles)

    def test_empty_code(self) -> None:
        assert render_code_snippet("") == []


class TestRenderDiffSnippet:
    def test_added_lines_green(self) -> None:
        patch = "@@ -1,2 +1,3 @@\n context\n+added line\n context\n"
        lines = render_diff_snippet(patch)
        text = "".join(t for _, t in lines)
        assert "added line" in text
        styles = [s for s, t in lines if "added" in t]
        assert any("green" in s for s in styles)

    def test_removed_lines_red(self) -> None:
        patch = "@@ -1,3 +1,2 @@\n context\n-removed line\n context\n"
        lines = render_diff_snippet(patch)
        styles = [s for s, t in lines if "removed" in t]
        assert any("red" in s for s in styles)

    def test_truncation(self) -> None:
        long_patch = "@@ -1,50 +1,50 @@\n" + "".join(f"+line{i}\n" for i in range(50))
        lines = render_diff_snippet(long_patch, max_lines=10)
        text = "".join(t for _, t in lines)
        assert "more lines" in text

    def test_empty_patch(self) -> None:
        assert render_diff_snippet("") == []


class TestRenderSuggestionAsDiff:
    def test_code_block(self) -> None:
        suggestion = "Replace with:\n```python\nx = safe()\n```\n"
        lines = render_suggestion_as_diff(suggestion)
        text = "".join(t for _, t in lines)
        assert "x = safe()" in text
        assert "Replace with" in text

    def test_no_code_block(self) -> None:
        suggestion = "Just a plain text suggestion."
        lines = render_suggestion_as_diff(suggestion)
        text = "".join(t for _, t in lines)
        assert "plain text" in text

    def test_empty(self) -> None:
        assert render_suggestion_as_diff("") == []
