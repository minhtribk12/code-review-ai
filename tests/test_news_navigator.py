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

    def test_toggle_select(self) -> None:
        viewer = NewsViewer([_make_article(0), _make_article(1)])
        viewer.toggle_select()
        assert 0 in viewer.selected
        assert viewer.selected_count == 1
        viewer.toggle_select()
        assert 0 not in viewer.selected

    def test_select_all(self) -> None:
        viewer = NewsViewer([_make_article(i) for i in range(5)])
        viewer.select_all()
        assert viewer.selected_count == 5

    def test_clear_selection(self) -> None:
        viewer = NewsViewer([_make_article(0)])
        viewer.toggle_select()
        viewer.clear_selection()
        assert viewer.selected_count == 0

    def test_delete_current(self) -> None:
        store = MagicMock()
        articles = [_make_article(0), _make_article(1), _make_article(2)]
        viewer = NewsViewer(articles, store=store)
        viewer.cursor = 1
        viewer.delete_current()
        assert len(viewer.articles) == 2
        store.delete_article.assert_called_once()
        assert viewer.status_message == "Deleted"

    def test_delete_selected(self) -> None:
        store = MagicMock()
        store.delete_articles.return_value = 2
        articles = [_make_article(i) for i in range(5)]
        viewer = NewsViewer(articles, store=store)
        viewer.selected = {1, 3}
        viewer.delete_selected()
        assert len(viewer.articles) == 3
        assert viewer.selected_count == 0

    def test_mark_selected_read(self) -> None:
        store = MagicMock()
        articles = [_make_article(i) for i in range(3)]
        viewer = NewsViewer(articles, store=store)
        viewer.selected = {0, 2}
        viewer.mark_selected_read()
        assert viewer.articles[0].is_read
        assert viewer.articles[2].is_read
        assert not viewer.articles[1].is_read
        assert viewer.selected_count == 0

    def test_mark_all_read(self) -> None:
        store = MagicMock()
        store.mark_all_read.return_value = 3
        articles = [_make_article(i) for i in range(3)]
        viewer = NewsViewer(articles, store=store)
        viewer.mark_all_read()
        assert all(a.is_read for a in viewer.articles)
        store.mark_all_read.assert_called_once()
