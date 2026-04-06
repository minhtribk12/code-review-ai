"""Tests for news reader bug fixes (BF-1 through BF-4)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from code_review_agent.news.content import (
    _fetch_reddit_content,
    fetch_article_content,
    is_valid_content,
)


class TestRedditJsonContent:
    """BF-1: Reddit content via JSON API."""

    def test_reddit_url_uses_json_endpoint(self) -> None:
        """Reddit URLs should use .json path, not HTML scraping."""
        with patch("code_review_agent.news.content.httpx") as mock:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = [
                {"data": {"children": [{"data": {"title": "Test", "selftext": "Body text"}}]}},
                {"data": {"children": []}},
            ]
            mock_resp.raise_for_status = MagicMock()
            mock.get.return_value = mock_resp

            url = "https://www.reddit.com/r/test/comments/abc123/test_post/"
            _html, text = fetch_article_content(url)
            assert "Body text" in text

            # Verify .json URL was called
            called_url = mock.get.call_args[0][0]
            assert called_url.endswith(".json")

    def test_reddit_json_extracts_selftext(self) -> None:
        with patch("code_review_agent.news.content.httpx") as mock:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = [
                {
                    "data": {
                        "children": [
                            {
                                "data": {
                                    "title": "Test Post",
                                    "selftext": "This is the &amp; post body with &gt; entities",
                                }
                            }
                        ]
                    }
                },
                {"data": {"children": []}},
            ]
            mock_resp.raise_for_status = MagicMock()
            mock.get.return_value = mock_resp

            _html, text = _fetch_reddit_content("https://reddit.com/r/test/comments/x/y/")
            assert "This is the & post body with > entities" in text

    def test_reddit_json_includes_top_comments(self) -> None:
        with patch("code_review_agent.news.content.httpx") as mock:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = [
                {"data": {"children": [{"data": {"title": "T", "selftext": "Body"}}]}},
                {
                    "data": {
                        "children": [
                            {
                                "kind": "t1",
                                "data": {"body": "Great insight here &amp; more", "ups": 50},
                            },
                            {
                                "kind": "t1",
                                "data": {"body": "Another good comment text", "ups": 30},
                            },
                        ]
                    }
                },
            ]
            mock_resp.raise_for_status = MagicMock()
            mock.get.return_value = mock_resp

            _html, text = _fetch_reddit_content("https://reddit.com/r/test/comments/x/y/")
            assert "Top Comments" in text
            assert "Great insight here & more" in text

    def test_non_reddit_url_uses_html_fetch(self) -> None:
        with patch("code_review_agent.news.content.httpx") as mock:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = "<html><body><p>Hello world</p></body></html>"
            mock_resp.raise_for_status = MagicMock()
            mock.get.return_value = mock_resp

            _html, text = fetch_article_content("https://example.com/article")
            assert "Hello world" in text


class TestHtmlEntityDecoding:
    """BF-3: HTML entities decoded in all sources."""

    def test_reddit_title_entities_decoded(self) -> None:
        from code_review_agent.news.sources.reddit import _search_reddit

        with patch("code_review_agent.news.sources.reddit.httpx") as mock:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "data": {
                    "children": [
                        {
                            "data": {
                                "id": "1",
                                "title": "Test &gt; Title &amp; More",
                                "url": "https://example.com",
                                "score": 10,
                                "num_comments": 5,
                                "subreddit": "test",
                                "created_utc": 1712000000,
                                "selftext": "Body &lt;here&gt;",
                            }
                        },
                    ]
                },
            }
            mock_resp.raise_for_status = MagicMock()
            mock.get.return_value = mock_resp

            items = _search_reddit("test", timeout=5)
            assert items[0].title == "Test > Title & More"
            assert "Body <here>" in items[0].summary

    def test_hn_title_entities_decoded(self) -> None:
        from code_review_agent.news.sources.hackernews import _search_stories

        with patch("code_review_agent.news.sources.hackernews.httpx") as mock:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "hits": [
                    {
                        "objectID": "1",
                        "title": "Show HN: A &amp; B",
                        "url": "https://example.com",
                        "points": 50,
                        "num_comments": 10,
                        "created_at_i": 1712000000,
                    },
                ]
            }
            mock_resp.raise_for_status = MagicMock()
            mock.get.return_value = mock_resp

            items = _search_stories("test", timeout=5)
            assert items[0].title == "Show HN: A & B"


class TestContentValidation:
    """BF-4: Content validation prevents garbled data."""

    def test_valid_content_accepted(self) -> None:
        assert is_valid_content("This is normal readable text with enough length.")

    def test_garbled_content_rejected(self) -> None:
        garbled = "\x00\x01\x02\x03\x04\x05" * 20
        assert not is_valid_content(garbled)

    def test_empty_content_rejected(self) -> None:
        assert not is_valid_content("")

    def test_short_content_rejected(self) -> None:
        assert not is_valid_content("Hi")

    def test_newlines_not_counted_as_control(self) -> None:
        text = "Line 1\nLine 2\nLine 3\n" * 5
        assert is_valid_content(text)

    def test_tabs_not_counted_as_control(self) -> None:
        text = "col1\tcol2\tcol3\n" * 5
        assert is_valid_content(text)


class TestThirtyDaysCommand:
    """BF-2: news 30days without topic shows usage."""

    def test_30days_without_topic_shows_usage(self) -> None:
        from unittest.mock import MagicMock

        from code_review_agent.news.commands import cmd_news

        session = MagicMock()
        with patch("code_review_agent.news.commands.console") as mock_con:
            cmd_news(["30days"], session)
            output = str(mock_con.print.call_args_list)
            assert "Usage" in output or "news 30days <topic>" in output
