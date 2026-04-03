"""Tests for background news fetch."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from code_review_agent.news.background import BackgroundNewsFetch


class TestBackgroundNewsFetch:
    def test_format_status_fetching(self) -> None:
        session = MagicMock()
        bg = BackgroundNewsFetch(domain="hackernews", session=session)
        status = bg.format_status_line()
        assert "hackernews" in status
        assert "fetching" in status.lower()

    def test_format_status_done(self) -> None:
        session = MagicMock()
        bg = BackgroundNewsFetch(domain="hackernews", session=session)
        bg._done.set()
        bg._phase = "done"
        bg._curated_count = 10
        status = bg.format_status_line()
        assert "10 articles" in status

    def test_format_status_failed(self) -> None:
        session = MagicMock()
        bg = BackgroundNewsFetch(domain="hackernews", session=session)
        bg._done.set()
        bg._phase = "failed"
        bg._error = "network error"
        status = bg.format_status_line()
        assert "failed" in status
        assert "network error" in status

    def test_is_running_before_start(self) -> None:
        session = MagicMock()
        bg = BackgroundNewsFetch(domain="test", session=session)
        assert bg.is_running  # not done yet

    def test_is_done_after_set(self) -> None:
        session = MagicMock()
        bg = BackgroundNewsFetch(domain="test", session=session)
        bg._done.set()
        assert bg.is_done
        assert not bg.is_running

    def test_curating_phase(self) -> None:
        session = MagicMock()
        bg = BackgroundNewsFetch(domain="ai", session=session)
        bg._phase = "curating"
        bg._article_count = 25
        status = bg.format_status_line()
        assert "curating" in status.lower()
        assert "25" in status
        assert "LLM" in status

    def test_result_initially_none(self) -> None:
        session = MagicMock()
        bg = BackgroundNewsFetch(domain="test", session=session)
        assert bg.result is None
        assert bg.synthesis == ""

    def test_worker_with_mock_fetch(self) -> None:
        """Test the worker completes with mocked RSS fetch."""
        session = MagicMock()
        session.effective_settings = MagicMock()

        from datetime import datetime

        from code_review_agent.news.models import Article

        mock_articles = [
            Article(
                id="test:1",
                domain="test",
                title="Test",
                url="https://example.com",
                fetched_at=datetime.now(),
            )
        ]

        with (
            patch(
                "code_review_agent.news.fetcher.fetch_news",
                return_value=mock_articles,
            ),
            patch("code_review_agent.news.storage.ArticleStore") as mock_store_cls,
        ):
            mock_store = MagicMock()
            mock_store_cls.return_value = mock_store

            bg = BackgroundNewsFetch(domain="test", session=session)
            bg._worker()

            assert bg.is_done
            assert bg._phase == "done"
            mock_store.save_articles.assert_called_once()
