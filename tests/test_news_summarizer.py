"""Tests for two-level LLM content summarization."""

from __future__ import annotations

from unittest.mock import MagicMock

from code_review_agent.news.summarizer import (
    BriefResponse,
    TakeawayResponse,
    extract_takeaways,
    fallback_brief,
    fallback_takeaways,
    format_takeaways,
    generate_structured_brief,
)


class TestTakeawayExtraction:
    def test_returns_takeaways(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete.return_value = TakeawayResponse(
            takeaways=["Point 1", "Point 2", "Point 3"],
        )
        result = extract_takeaways(
            "A long article about AI trends and technology in 2026.", mock_llm
        )
        assert len(result) == 3
        assert result[0] == "Point 1"

    def test_max_5_takeaways(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete.return_value = TakeawayResponse(
            takeaways=[f"Point {i}" for i in range(10)],
        )
        result = extract_takeaways(
            "A long article about AI with many interesting details.", mock_llm
        )
        assert len(result) <= 5

    def test_empty_content_returns_empty(self) -> None:
        mock_llm = MagicMock()
        assert extract_takeaways("", mock_llm) == []
        mock_llm.complete.assert_not_called()

    def test_handles_llm_failure(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete.side_effect = RuntimeError("LLM down")
        result = extract_takeaways(
            "Some article content here with enough text for validation.",
            mock_llm,
        )
        assert result == []

    def test_cached_after_first_call(self) -> None:
        """Verify the summarizer itself doesn't cache (caching is in navigator)."""
        mock_llm = MagicMock()
        mock_llm.complete.return_value = TakeawayResponse(takeaways=["A"])
        long = "Article about technology trends with enough content to pass validation."
        extract_takeaways(long, mock_llm)
        extract_takeaways(long, mock_llm)
        assert mock_llm.complete.call_count == 2  # no internal cache


class TestFallbackTakeaways:
    def test_returns_first_sentences(self) -> None:
        content = "\n".join(f"Sentence {i} is about something important." for i in range(10))
        result = fallback_takeaways(content, count=3)
        assert len(result) == 3

    def test_skips_short_lines(self) -> None:
        content = "Hi\nThis is a real sentence about something.\nOk\nAnother real sentence here."
        result = fallback_takeaways(content)
        assert all(len(s) >= 20 for s in result)

    def test_empty_content(self) -> None:
        assert fallback_takeaways("") == []


class TestFormatTakeaways:
    def test_bullet_format(self) -> None:
        formatted = format_takeaways(["Point A", "Point B"])
        assert "* Point A" in formatted
        assert "* Point B" in formatted

    def test_empty(self) -> None:
        assert format_takeaways([]) == ""


class TestStructuredBrief:
    def test_generates_brief(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete.return_value = BriefResponse(
            brief="## Topic\n\nStructured content here.\n\n## Details\n\nMore info.",
        )
        result = generate_structured_brief(
            "Raw article text about important technology trends.", mock_llm
        )
        assert "## Topic" in result
        assert "## Details" in result

    def test_empty_content(self) -> None:
        mock_llm = MagicMock()
        assert generate_structured_brief("", mock_llm) == ""
        mock_llm.complete.assert_not_called()

    def test_handles_llm_failure(self) -> None:
        mock_llm = MagicMock()
        mock_llm.complete.side_effect = RuntimeError("fail")
        result = generate_structured_brief(
            "Article content about technology and AI with details.", mock_llm
        )
        assert result == ""


class TestFallbackBrief:
    def test_auto_paragraphs(self) -> None:
        content = "Line one.\nLine two.\n\nLine three after break."
        result = fallback_brief(content)
        assert "Line one. Line two." in result
        assert "\n\n" in result

    def test_empty(self) -> None:
        assert fallback_brief("") == ""


class TestStorageNewColumns:
    def test_takeaways_column_exists(self, tmp_path: object) -> None:
        from pathlib import Path

        from code_review_agent.news.storage import ArticleStore

        store = ArticleStore(db_path=Path(str(tmp_path)) / "test.db")
        from datetime import datetime

        from code_review_agent.news.models import Article

        a = Article(
            id="t:1",
            domain="hn",
            title="T",
            url="u",
            fetched_at=datetime.now(),
        )
        store.save_articles([a])
        store.update_takeaways("t:1", "* Point 1\n* Point 2")
        loaded = store.load_articles()
        assert loaded[0].key_takeaways == "* Point 1\n* Point 2"

    def test_brief_column_exists(self, tmp_path: object) -> None:
        from pathlib import Path

        from code_review_agent.news.storage import ArticleStore

        store = ArticleStore(db_path=Path(str(tmp_path)) / "test.db")
        from datetime import datetime

        from code_review_agent.news.models import Article

        a = Article(
            id="t:1",
            domain="hn",
            title="T",
            url="u",
            fetched_at=datetime.now(),
        )
        store.save_articles([a])
        store.update_structured_brief("t:1", "## Heading\n\nContent")
        loaded = store.load_articles()
        assert loaded[0].structured_brief == "## Heading\n\nContent"
