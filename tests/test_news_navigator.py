"""Tests for news navigator state machine."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

from code_review_agent.news.models import Article
from code_review_agent.news.navigator import NewsViewer


def _make_article(
    index: int,
    is_read: bool = False,
    is_saved: bool = False,
    score: int = 50,
) -> Article:
    return Article(
        id=f"test:{index}",
        domain="test",
        title=f"Article {index}",
        url=f"https://example.com/{index}",
        score=score,
        is_read=is_read,
        is_saved=is_saved,
        fetched_at=datetime.now(),
    )


class TestNewsViewer:
    def test_move_up_down(self) -> None:
        viewer = NewsViewer([_make_article(i) for i in range(5)])
        assert viewer.cursor == 0
        viewer.move_down()
        assert viewer.cursor == 1
        viewer.move_up()
        assert viewer.cursor == 0

    def test_move_up_at_top(self) -> None:
        viewer = NewsViewer([_make_article(0)])
        viewer.move_up()
        assert viewer.cursor == 0

    def test_move_down_at_bottom(self) -> None:
        viewer = NewsViewer([_make_article(0)])
        viewer.move_down()
        assert viewer.cursor == 0

    def test_toggle_detail(self) -> None:
        viewer = NewsViewer([_make_article(0)])
        assert not viewer.is_detail_open
        viewer.toggle_detail()
        assert viewer.is_detail_open
        viewer.toggle_detail()
        assert not viewer.is_detail_open

    def test_toggle_save(self) -> None:
        store = MagicMock()
        viewer = NewsViewer([_make_article(0)], store=store)
        viewer.toggle_save()
        store.mark_saved.assert_called_once()
        assert viewer.articles[0].is_saved

    def test_mark_read(self) -> None:
        store = MagicMock()
        viewer = NewsViewer([_make_article(0)], store=store)
        viewer.mark_read()
        store.mark_read.assert_called_once_with("test:0")
        assert viewer.articles[0].is_read

    def test_mark_read_already_read(self) -> None:
        store = MagicMock()
        viewer = NewsViewer([_make_article(0, is_read=True)], store=store)
        viewer.mark_read()
        store.mark_read.assert_not_called()

    def test_unread_count(self) -> None:
        viewer = NewsViewer(
            [
                _make_article(0, is_read=False),
                _make_article(1, is_read=True),
                _make_article(2, is_read=False),
            ]
        )
        assert viewer.unread_count == 2

    def test_saved_count(self) -> None:
        viewer = NewsViewer(
            [
                _make_article(0, is_saved=True),
                _make_article(1, is_saved=False),
            ]
        )
        assert viewer.saved_count == 1

    def test_current_article(self) -> None:
        articles = [_make_article(0), _make_article(1)]
        viewer = NewsViewer(articles)
        assert viewer.current_article == articles[0]
        viewer.move_down()
        assert viewer.current_article == articles[1]

    def test_empty_articles(self) -> None:
        viewer = NewsViewer([])
        assert viewer.current_article is None
        assert viewer.unread_count == 0
        viewer.move_down()  # should not crash
        viewer.toggle_save()  # should not crash
