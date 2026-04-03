"""Tests for news article model."""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import ValidationError

from code_review_agent.news.models import Article, ArticlePriority


class TestArticle:
    def test_frozen(self) -> None:
        import pytest

        article = Article(id="hn:1", domain="hackernews", title="Test", url="https://example.com")
        with pytest.raises(ValidationError):
            article.title = "changed"  # type: ignore[misc]

    def test_priority_trending(self) -> None:
        article = Article(id="hn:1", domain="hn", title="Test", url="", score=500)
        assert article.priority == ArticlePriority.TRENDING

    def test_priority_saved(self) -> None:
        article = Article(id="hn:1", domain="hn", title="Test", url="", is_saved=True)
        assert article.priority == ArticlePriority.SAVED

    def test_priority_read(self) -> None:
        article = Article(id="hn:1", domain="hn", title="Test", url="", is_read=True, score=0)
        assert article.priority == ArticlePriority.READ

    def test_priority_recent(self) -> None:
        article = Article(id="hn:1", domain="hn", title="Test", url="", score=10)
        assert article.priority == ArticlePriority.RECENT

    def test_age_display_minutes(self) -> None:
        article = Article(
            id="hn:1",
            domain="hn",
            title="Test",
            url="",
            published_at=datetime.now() - timedelta(minutes=30),
        )
        assert article.age_display == "30m"

    def test_age_display_hours(self) -> None:
        article = Article(
            id="hn:1",
            domain="hn",
            title="Test",
            url="",
            published_at=datetime.now() - timedelta(hours=5),
        )
        assert article.age_display == "5h"

    def test_age_display_days(self) -> None:
        article = Article(
            id="hn:1",
            domain="hn",
            title="Test",
            url="",
            published_at=datetime.now() - timedelta(days=3),
        )
        assert article.age_display == "3d"

    def test_age_display_none(self) -> None:
        article = Article(id="hn:1", domain="hn", title="Test", url="")
        assert article.age_display == ""

    def test_score_display_thousands(self) -> None:
        article = Article(id="hn:1", domain="hn", title="Test", url="", score=1234)
        assert article.score_display == "1.2k"

    def test_score_display_small(self) -> None:
        article = Article(id="hn:1", domain="hn", title="Test", url="", score=42)
        assert article.score_display == "42"
