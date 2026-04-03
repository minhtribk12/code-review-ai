"""Tests for auto-fix: parse suggestions and apply code patches."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from code_review_agent.auto_fix import (
    apply_fix,
    parse_suggestion,
    preview_fix,
    undo_fix,
)


class TestParseSuggestion:
    """Test extracting fixes from suggestion text."""

    def test_single_code_block(self) -> None:
        suggestion = "Replace with:\n```python\nx = safe_value\n```"
        fix = parse_suggestion(suggestion, "app.py", 10)
        assert fix is not None
        assert fix.replacement_code == "x = safe_value"
        assert fix.original_code is None

    def test_two_code_blocks(self) -> None:
        suggestion = "Change:\n```python\neval(x)\n```\nTo:\n```python\nast.literal_eval(x)\n```"
        fix = parse_suggestion(suggestion, "app.py", 5)
        assert fix is not None
        assert fix.original_code == "eval(x)"
        assert fix.replacement_code == "ast.literal_eval(x)"

    def test_no_code_block_returns_none(self) -> None:
        assert parse_suggestion("just some text", "app.py", 1) is None

    def test_none_suggestion_returns_none(self) -> None:
        assert parse_suggestion(None, "app.py", 1) is None

    def test_none_file_path_returns_none(self) -> None:
        assert parse_suggestion("```\ncode\n```", None, 1) is None


class TestPreviewFix:
    """Test fix preview formatting."""

    def test_preview_with_original(self) -> None:
        from code_review_agent.auto_fix import ParsedFix

        fix = ParsedFix("app.py", 10, "old code", "new code", "Fix it")
        preview = preview_fix(fix)
        assert "app.py" in preview
        assert "Line: 10" in preview
        assert "old code" in preview
        assert "new code" in preview

    def test_preview_without_original(self) -> None:
        from code_review_agent.auto_fix import ParsedFix

        fix = ParsedFix("app.py", None, None, "new code", "Fix it")
        preview = preview_fix(fix)
        assert "+++ Replacement" in preview
        assert "--- Original" not in preview


class TestApplyFix:
    """Test applying fixes to source files."""

    def test_apply_with_original_code(self, tmp_path: Path) -> None:
        from code_review_agent.auto_fix import ParsedFix

        source = tmp_path / "app.py"
        source.write_text("x = eval(input())\ny = 2\n")

        fix = ParsedFix("app.py", 1, "eval(input())", "safe_eval(input())", "Fix eval")
        result = apply_fix(fix, tmp_path)

        assert result.is_applied
        assert "safe_eval" in source.read_text()
        assert result.backup_path is not None

    def test_apply_by_line_number(self, tmp_path: Path) -> None:
        from code_review_agent.auto_fix import ParsedFix

        source = tmp_path / "app.py"
        source.write_text("line1\nline2\nline3\n")

        fix = ParsedFix("app.py", 2, None, "fixed_line2", "Fix line 2")
        result = apply_fix(fix, tmp_path)

        assert result.is_applied
        assert "fixed_line2" in source.read_text()

    def test_file_not_found(self, tmp_path: Path) -> None:
        from code_review_agent.auto_fix import ParsedFix

        fix = ParsedFix("nonexistent.py", 1, None, "code", "Fix")
        result = apply_fix(fix, tmp_path)
        assert not result.is_applied
        assert "not found" in result.message

    def test_backup_created(self, tmp_path: Path) -> None:
        from pathlib import Path as P

        from code_review_agent.auto_fix import ParsedFix

        source = tmp_path / "app.py"
        source.write_text("original content\n")

        fix = ParsedFix("app.py", 1, "original content", "new content", "Fix")
        result = apply_fix(fix, tmp_path)

        assert result.backup_path is not None
        assert P(result.backup_path).exists()
        assert "original content" in P(result.backup_path).read_text()


class TestUndoFix:
    """Test restoring from backup."""

    def test_undo_restores_file(self, tmp_path: Path) -> None:
        source = tmp_path / "app.py"
        source.write_text("original\n")
        backup = tmp_path / "app.py.bak"
        backup.write_text("original\n")

        source.write_text("modified\n")
        assert undo_fix(str(backup), source)
        assert source.read_text() == "original\n"

    def test_undo_missing_backup(self, tmp_path: Path) -> None:
        source = tmp_path / "app.py"
        source.write_text("content\n")
        assert not undo_fix("/nonexistent/backup", source)
