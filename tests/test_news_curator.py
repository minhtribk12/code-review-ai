"""Tests for LLM-powered news curation."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

from code_review_agent.news.curator import (
    CuratedArticle,
    CurationResponse,
    _fallback_curation,
    curate_articles,
    format_articles_for_llm,
)
from code_review_agent.news.models import Article


def _make_article(
    index: int,
    title: str = "Test Article",
    score: int = 50,
) -> Article:
    return Article(
        id=f"test:{index}",
        domain="test",
        title=f"{title} {index}",
        url=f"https://example.com/{index}",
        score=score,
        comment_count=index * 10,
        tags=("python", "ai"),
        summary=f"Summary for article {index}",
        fetched_at=datetime.now(),
    )


class TestFormatArticlesForLLM:
    def test_includes_title_and_score(self) -> None:
        articles = [_make_article(1, score=100)]
        text = format_articles_for_llm(articles)
        assert "[0]" in text
        assert "Test Article 1" in text
        assert "score:100" in text

    def test_includes_url(self) -> None:
        articles = [_make_article(1)]
        text = format_articles_for_llm(articles)
        assert "url:https://example.com/1" in text

    def test_includes_tags(self) -> None:
        articles = [_make_article(1)]
        text = format_articles_for_llm(articles)
        assert "python" in text

    def test_multiple_articles(self) -> None:
        articles = [_make_article(i) for i in range(3)]
        text = format_articles_for_llm(articles)
        assert "[0]" in text
        assert "[1]" in text
        assert "[2]" in text


class TestFallbackCuration:
    def test_sorts_by_score(self) -> None:
        articles = [_make_article(1, score=10), _make_article(2, score=100)]
        result = _fallback_curation(articles)
        assert result.curated_articles[0].title == "Test Article 2"

    def test_limits_to_15(self) -> None:
        articles = [_make_article(i) for i in range(30)]
        result = _fallback_curation(articles)
        assert len(result.curated_articles) <= 15

    def test_empty_input(self) -> None:
        result = _fallback_curation([])
        assert result.curated_articles == []


class TestCurateArticles:
    def test_successful_curation(self) -> None:
        articles = [_make_article(1)]
        mock_llm = MagicMock()
        mock_llm.complete.return_value = CurationResponse(
            curated_articles=[
                CuratedArticle(
                    title="Curated Title",
                    summary="AI summary",
                    relevance_score=85,
                    article_index=0,
                ),
            ],
            synthesis="One trending story today.",
        )
        result = curate_articles(articles, mock_llm, "test")
        assert len(result.curated_articles) == 1
        assert result.curated_articles[0].summary == "AI summary"

    def test_falls_back_on_error(self) -> None:
        articles = [_make_article(1, score=50)]
        mock_llm = MagicMock()
        mock_llm.complete.side_effect = RuntimeError("LLM down")
        result = curate_articles(articles, mock_llm, "test")
        assert len(result.curated_articles) == 1

    def test_empty_articles(self) -> None:
        mock_llm = MagicMock()
        result = curate_articles([], mock_llm, "test")
        assert result.curated_articles == []
        mock_llm.complete.assert_not_called()


class TestCuratedArticle:
    def test_frozen(self) -> None:
        import pytest
        from pydantic import ValidationError

        ca = CuratedArticle(title="T", summary="S", relevance_score=50)
        with pytest.raises(ValidationError):
            ca.title = "changed"  # type: ignore[misc]
