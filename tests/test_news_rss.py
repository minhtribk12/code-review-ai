"""Tests for RSS adapter."""

from __future__ import annotations

from unittest.mock import patch

from code_review_agent.news.adapters.rss import _clean_text, _extract_id, fetch_rss
from code_review_agent.news.domains import DomainConfig


class TestCleanText:
    def test_strips_html_tags(self) -> None:
        assert _clean_text("<p>Hello <b>world</b></p>") == "Hello world"

    def test_collapses_whitespace(self) -> None:
        assert _clean_text("  hello   world  ") == "hello world"

    def test_empty(self) -> None:
        assert _clean_text("") == ""


class TestExtractId:
    def test_from_id(self) -> None:
        entry = {"id": "https://example.com/post/12345"}
        assert _extract_id(entry) == "12345"

    def test_from_link(self) -> None:
        entry = {"link": "https://example.com/article"}
        assert _extract_id(entry) == "article"

    def test_truncates_long_ids(self) -> None:
        entry = {"id": "a" * 100}
        assert len(_extract_id(entry)) <= 64


class TestFetchRss:
    def test_returns_empty_on_error(self) -> None:
        config = DomainConfig("test", "Test", "https://invalid.example.com/feed")
        with patch("code_review_agent.news.adapters.rss.feedparser") as mock_fp:
            mock_fp.parse.side_effect = Exception("network error")
            result = fetch_rss(config)
        assert result == []

    def test_parses_entries(self) -> None:
        config = DomainConfig("test", "Test Feed", "https://example.com/feed")

        class MockFeed:
            bozo = False
            entries = [
                {
                    "id": "1",
                    "title": "Test Article",
                    "link": "https://example.com/1",
                    "author": "tester",
                    "summary": "A test article summary",
                    "tags": [{"term": "python"}],
                },
            ]

        with patch("code_review_agent.news.adapters.rss.feedparser") as mock_fp:
            mock_fp.parse.return_value = MockFeed()
            articles = fetch_rss(config)

        assert len(articles) == 1
        assert articles[0].title == "Test Article"
        assert articles[0].domain == "test"
        assert articles[0].author == "tester"
        assert "python" in articles[0].tags
