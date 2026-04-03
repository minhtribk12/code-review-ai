"""Tests for news article storage."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from code_review_agent.news.models import Article
from code_review_agent.news.storage import ArticleStore


def _make_article(
    article_id: str = "hn:1", domain: str = "hackernews", title: str = "Test"
) -> Article:
    return Article(
        id=article_id,
        domain=domain,
        title=title,
        url=f"https://example.com/{article_id}",
        fetched_at=datetime.now(),
    )


class TestArticleStore:
    def test_save_and_load(self, tmp_path: Path) -> None:
        store = ArticleStore(db_path=tmp_path / "test.db")
        articles = [_make_article("hn:1"), _make_article("hn:2")]
        count = store.save_articles(articles)
        assert count == 2

        loaded = store.load_articles()
        assert len(loaded) == 2
        assert loaded[0].id in ("hn:1", "hn:2")

    def test_load_by_domain(self, tmp_path: Path) -> None:
        store = ArticleStore(db_path=tmp_path / "test.db")
        store.save_articles(
            [
                _make_article("hn:1", domain="hackernews"),
                _make_article("dev:1", domain="devto"),
            ]
        )
        loaded = store.load_articles(domain="hackernews")
        assert len(loaded) == 1
        assert loaded[0].domain == "hackernews"

    def test_upsert_updates_score(self, tmp_path: Path) -> None:
        store = ArticleStore(db_path=tmp_path / "test.db")
        store.save_articles([_make_article("hn:1")])
        updated = Article(
            id="hn:1",
            domain="hackernews",
            title="Test",
            url="https://example.com/hn:1",
            score=100,
            fetched_at=datetime.now(),
        )
        store.save_articles([updated])
        loaded = store.load_articles()
        assert loaded[0].score == 100

    def test_mark_read(self, tmp_path: Path) -> None:
        store = ArticleStore(db_path=tmp_path / "test.db")
        store.save_articles([_make_article("hn:1")])
        store.mark_read("hn:1")
        loaded = store.load_articles()
        assert loaded[0].is_read

    def test_mark_saved(self, tmp_path: Path) -> None:
        store = ArticleStore(db_path=tmp_path / "test.db")
        store.save_articles([_make_article("hn:1")])
        store.mark_saved("hn:1", saved=True)
        loaded = store.load_articles(saved_only=True)
        assert len(loaded) == 1
        assert loaded[0].is_saved

    def test_unread_count(self, tmp_path: Path) -> None:
        store = ArticleStore(db_path=tmp_path / "test.db")
        store.save_articles([_make_article("hn:1"), _make_article("hn:2")])
        assert store.get_unread_count() == 2
        store.mark_read("hn:1")
        assert store.get_unread_count() == 1

    def test_update_content(self, tmp_path: Path) -> None:
        store = ArticleStore(db_path=tmp_path / "test.db")
        store.save_articles([_make_article("hn:1")])
        store.update_content("hn:1", "<p>Hello</p>", "Hello")
        loaded = store.load_articles()
        assert loaded[0].content_text == "Hello"

    def test_get_stats(self, tmp_path: Path) -> None:
        store = ArticleStore(db_path=tmp_path / "test.db")
        store.save_articles(
            [
                _make_article("hn:1", domain="hackernews"),
                _make_article("hn:2", domain="hackernews"),
                _make_article("dev:1", domain="devto"),
            ]
        )
        store.mark_read("hn:1")
        store.mark_saved("dev:1", saved=True)
        stats = store.get_stats()
        assert stats["hackernews"]["total"] == 2
        assert stats["hackernews"]["read"] == 1
        assert stats["devto"]["saved"] == 1

    def test_read_position(self, tmp_path: Path) -> None:
        store = ArticleStore(db_path=tmp_path / "test.db")
        store.save_articles([_make_article("hn:1")])
        store.update_read_position("hn:1", 0.42)
        loaded = store.load_articles()
        assert loaded[0].read_position == 0.42
