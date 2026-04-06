"""Tests for background news fetch."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from code_review_agent.news.background import BackgroundNewsFetch


class TestBackgroundNewsFetch:
    def test_format_status_initial(self) -> None:
        session = MagicMock()
        bg = BackgroundNewsFetch(domain="hackernews", session=session)
        status = bg.format_status_line()
        assert "hackernews" in status or "query" in status.lower()

    def test_format_status_done(self) -> None:
        session = MagicMock()
        bg = BackgroundNewsFetch(domain="hackernews", session=session)
        bg._done.set()
        bg._phase = "done"
        bg._curated_count = 10
        status = bg.format_status_line()
        assert "10" in status

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
        bg._deduped_count = 25
        status = bg.format_status_line()
        assert "25" in status
        assert "LLM" in status or "synthesis" in status.lower()

    def test_result_initially_none(self) -> None:
        session = MagicMock()
        bg = BackgroundNewsFetch(domain="test", session=session)
        assert bg.result is None
        assert bg.synthesis == ""

    def test_worker_with_mock_sources(self) -> None:
        """Test the pipeline completes with mocked source adapters."""
        session = MagicMock()
        session.effective_settings = MagicMock()

        from datetime import datetime

        from code_review_agent.news.sources import RawNewsItem

        mock_items = [
            RawNewsItem(
                source="hackernews",
                external_id="1",
                title="Test Article",
                url="https://example.com",
                score=50,
                comment_count=10,
                published_at=datetime.now(),
            ),
        ]

        with (
            patch(
                "code_review_agent.news.sources.hackernews.fetch",
                return_value=mock_items,
            ),
            patch(
                "code_review_agent.news.sources.reddit.fetch",
                return_value=[],
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
