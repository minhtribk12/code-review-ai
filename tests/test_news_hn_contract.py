"""Contract tests for Hacker News adapter (mock HTTP)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from code_review_agent.news.query import preprocess_query
from code_review_agent.news.sources import RawNewsItem
from code_review_agent.news.sources.hackernews import fetch, health_check

_HN_FIXTURE = {
    "hits": [
        {
            "objectID": "12345",
            "title": "Show HN: A new Rust framework",
            "url": "https://example.com/rust-framework",
            "author": "rustdev",
            "points": 234,
            "num_comments": 89,
            "created_at_i": 1712000000,
        },
        {
            "objectID": "12346",
            "title": "Why async Rust matters",
            "url": "https://blog.example.com/async-rust",
            "author": "asyncfan",
            "points": 156,
            "num_comments": 45,
            "created_at_i": 1712100000,
        },
    ],
    "nbHits": 2,
}


class TestHNFetch:
    def test_returns_raw_news_items(self) -> None:
        query = preprocess_query("rust async")
        with patch("code_review_agent.news.sources.hackernews.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = _HN_FIXTURE
            mock_resp.raise_for_status = MagicMock()
            mock_httpx.get.return_value = mock_resp

            items = fetch(query, timeout=5)

        assert len(items) == 2
        assert all(isinstance(i, RawNewsItem) for i in items)
        assert all(i.source == "hackernews" for i in items)
        assert items[0].title == "Show HN: A new Rust framework"
        assert items[0].score == 234
        assert items[0].comment_count == 89

    def test_urls_are_valid(self) -> None:
        query = preprocess_query("test")
        with patch("code_review_agent.news.sources.hackernews.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = _HN_FIXTURE
            mock_resp.raise_for_status = MagicMock()
            mock_httpx.get.return_value = mock_resp

            items = fetch(query, timeout=5)

        assert all(i.url.startswith("http") for i in items)

    def test_handles_empty_response(self) -> None:
        query = preprocess_query("test")
        with patch("code_review_agent.news.sources.hackernews.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"hits": [], "nbHits": 0}
            mock_resp.raise_for_status = MagicMock()
            mock_httpx.get.return_value = mock_resp

            items = fetch(query, timeout=5)

        assert items == []

    def test_handles_timeout(self) -> None:
        query = preprocess_query("test")
        with patch("code_review_agent.news.sources.hackernews.httpx") as mock_httpx:
            mock_httpx.get.side_effect = httpx.TimeoutException("timeout")
            items = fetch(query, timeout=1)

        assert items == []

    def test_handles_http_error(self) -> None:
        query = preprocess_query("test")
        with patch("code_review_agent.news.sources.hackernews.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "500", request=MagicMock(), response=MagicMock()
            )
            mock_httpx.get.return_value = mock_resp
            items = fetch(query, timeout=5)

        assert items == []

    def test_date_confidence_is_high(self) -> None:
        query = preprocess_query("test")
        with patch("code_review_agent.news.sources.hackernews.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = _HN_FIXTURE
            mock_resp.raise_for_status = MagicMock()
            mock_httpx.get.return_value = mock_resp

            items = fetch(query, timeout=5)

        assert all(i.date_confidence == "high" for i in items)


class TestHNHealthCheck:
    def test_healthy(self) -> None:
        with patch("code_review_agent.news.sources.hackernews.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_httpx.head.return_value = mock_resp
            assert health_check()

    def test_unhealthy(self) -> None:
        with patch("code_review_agent.news.sources.hackernews.httpx") as mock_httpx:
            mock_httpx.head.side_effect = httpx.ConnectError("failed")
            assert not health_check()
