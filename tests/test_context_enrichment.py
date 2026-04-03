"""Tests for diff-aware context enrichment."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from code_review_agent.context_enrichment import (
    EnrichedDiff,
    enrich_diff_file,
    extract_imports,
    find_enclosing_scope,
    format_enriched_context,
    get_context_window,
    parse_diff_hunks,
)


class TestParseDiffHunks:
    """Test unified diff parsing."""

    def test_single_hunk(self) -> None:
        patch = "@@ -10,3 +10,4 @@\n context\n+added line\n context\n"
        hunks = parse_diff_hunks(patch)
        assert len(hunks) == 1
        assert hunks[0].start_line == 10
        assert hunks[0].line_count == 4
        assert 11 in hunks[0].changed_line_numbers

    def test_multiple_hunks(self) -> None:
        patch = (
            "@@ -1,3 +1,4 @@\n context\n+line1\n context\n@@ -20,2 +21,3 @@\n context\n+line2\n"
        )
        hunks = parse_diff_hunks(patch)
        assert len(hunks) == 2
        assert hunks[0].start_line == 1
        assert hunks[1].start_line == 21

    def test_removed_lines_dont_affect_numbers(self) -> None:
        patch = "@@ -5,4 +5,3 @@\n context\n-removed\n+added\n context\n"
        hunks = parse_diff_hunks(patch)
        assert len(hunks) == 1
        assert len(hunks[0].changed_line_numbers) == 1

    def test_empty_patch(self) -> None:
        assert parse_diff_hunks("") == []


class TestFindEnclosingScope:
    """Test function/class detection from source lines."""

    def test_python_function(self) -> None:
        lines = [
            "class Foo:",
            "    def bar(self):",
            "        x = 1",
            "        y = 2",
        ]
        func, cls = find_enclosing_scope(lines, 3, ".py")
        assert func == "bar"
        assert cls == "Foo"

    def test_python_async_function(self) -> None:
        lines = [
            "async def fetch_data():",
            "    response = await get()",
        ]
        func, _ = find_enclosing_scope(lines, 2, ".py")
        assert func == "fetch_data"

    def test_no_enclosing_scope(self) -> None:
        lines = ["x = 1", "y = 2"]
        func, cls = find_enclosing_scope(lines, 1, ".py")
        assert func is None
        assert cls is None

    def test_go_function(self) -> None:
        lines = [
            "func (s *Server) HandleRequest(w http.ResponseWriter) {",
            "    w.Write([]byte(s.name))",
            "}",
        ]
        func, _ = find_enclosing_scope(lines, 2, ".go")
        assert func == "HandleRequest"

    def test_unsupported_extension(self) -> None:
        lines = ["some code"]
        func, cls = find_enclosing_scope(lines, 1, ".xyz")
        assert func is None
        assert cls is None


class TestExtractImports:
    """Test import extraction."""

    def test_python_imports(self) -> None:
        lines = [
            "from __future__ import annotations",
            "import os",
            "from pathlib import Path",
            "",
            "def main():",
        ]
        imports = extract_imports(lines, ".py")
        assert "import os" in imports
        assert "from pathlib" in imports
        assert "def main" not in imports

    def test_no_imports(self) -> None:
        lines = ["x = 1"]
        assert extract_imports(lines, ".py") == ""

    def test_js_imports(self) -> None:
        lines = [
            "import React from 'react';",
            "import { useState } from 'react';",
            "",
            "function App() {",
        ]
        imports = extract_imports(lines, ".js")
        assert "React" in imports
        assert "function" not in imports


class TestGetContextWindow:
    """Test context window extraction."""

    def test_normal_range(self) -> None:
        lines = [f"line {i}" for i in range(20)]
        before, after = get_context_window(lines, 10, 12, context_lines=3)
        assert "line 7" in before
        assert "line 12" in after

    def test_clamped_to_start(self) -> None:
        lines = [f"line {i}" for i in range(10)]
        before, _ = get_context_window(lines, 3, 5, context_lines=10)
        # context_lines=10 but only 2 lines available before start_line=3
        assert "line 0" in before
        assert "line 1" in before


class TestEnrichDiffFile:
    """Test full enrichment pipeline."""

    def test_without_source(self) -> None:
        patch = "@@ -1,3 +1,4 @@\n context\n+added\n context\n"
        result = enrich_diff_file("test.py", patch)
        assert isinstance(result, EnrichedDiff)
        assert result.filename == "test.py"
        assert result.original_patch == patch

    def test_with_source(self, tmp_path: Path) -> None:
        source = tmp_path / "app.py"
        source.write_text(
            "import os\n\nclass Service:\n    def process(self):\n"
            "        x = 1\n        y = 2\n        z = 3\n"
        )
        patch = (
            "@@ -4,3 +4,4 @@\n     def process(self):\n"
            "         x = 1\n+        new_line\n         y = 2\n"
        )
        result = enrich_diff_file("app.py", patch, source_root=tmp_path)
        assert result.imports_section == "import os"
        assert len(result.hunks) == 1
        assert result.hunks[0].enclosing_function == "process"
        assert result.hunks[0].enclosing_class == "Service"


class TestFormatEnrichedContext:
    """Test prompt formatting."""

    def test_empty_enrichment(self) -> None:
        enriched = EnrichedDiff(
            filename="test.py", original_patch="", hunks=(), imports_section=""
        )
        assert format_enriched_context(enriched) == ""

    def test_includes_markers(self, tmp_path: Path) -> None:
        source = tmp_path / "app.py"
        source.write_text("import os\n\ndef main():\n    x = 1\n    y = 2\n")
        patch = "@@ -3,2 +3,3 @@\n def main():\n     x = 1\n+    z = 3\n     y = 2\n"
        enriched = enrich_diff_file("app.py", patch, source_root=tmp_path)
        formatted = format_enriched_context(enriched)
        assert "[CONTEXT: imports]" in formatted
        assert "[CHANGED:" in formatted
        assert "import os" in formatted
