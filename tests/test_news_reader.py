"""Tests for full-screen article reader."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from code_review_agent.news.models import Article
from code_review_agent.news.reader import ArticleReader


def _art(
    title: str = "Test",
    summary: str = "A test summary.",
    content_text: str = "",
    read_position: float = 0.0,
) -> Article:
    return Article(
        id="t:1",
        domain="hn",
        title=title,
        url="https://example.com",
        summary=summary,
        content_text=content_text,
        fetched_at=datetime.now(),
        read_position=read_position,
    )


class TestArticleReader:
    def test_uses_cached_content(self) -> None:
        r = ArticleReader(_art(content_text="L1\nL2\nL3"))
        assert not r.is_loading
        assert len(r.content_lines) == 3

    def test_falls_back_to_summary(self) -> None:
        with patch("code_review_agent.news.content.fetch_article_content", return_value=("", "")):
            r = ArticleReader(_art(summary="Summary text"))
        assert "Summary" in r.content_lines[0]

    def test_scroll_down_up(self) -> None:
        r = ArticleReader(_art(content_text="\n".join(f"L{i}" for i in range(100))))
        r.scroll_down(5)
        assert r.scroll_offset == 5
        r.scroll_up(3)
        assert r.scroll_offset == 2

    def test_scroll_clamped(self) -> None:
        r = ArticleReader(_art(content_text="Short"))
        r.scroll_up(100)
        assert r.scroll_offset == 0

    def test_top_bottom(self) -> None:
        r = ArticleReader(_art(content_text="\n".join(f"L{i}" for i in range(100))))
        r.scroll_to_bottom()
        assert r.scroll_offset > 0
        r.scroll_to_top()
        assert r.scroll_offset == 0

    def test_progress(self) -> None:
        r = ArticleReader(_art(content_text="\n".join(f"L{i}" for i in range(100))))
        assert r.progress > 0
        r.scroll_to_bottom()
        assert r.progress == 1.0

    def test_page_indicator(self) -> None:
        r = ArticleReader(_art(content_text="\n".join(f"L{i}" for i in range(100))))
        assert "/" in r.page_indicator

    def test_reading_time(self) -> None:
        r = ArticleReader(_art(content_text=" ".join(["word"] * 400)))
        assert "min read" in r.reading_time

    def test_next_prev(self) -> None:
        arts = [_art(title="A"), _art(title="B")]
        r = ArticleReader(arts[0], articles=arts, article_index=0)
        assert r.next_article()
        assert r.article.title == "B"
        assert r.prev_article()
        assert r.article.title == "A"

    def test_next_at_end(self) -> None:
        r = ArticleReader(_art(), articles=[_art()], article_index=0)
        assert not r.next_article()

    def test_prev_at_start(self) -> None:
        r = ArticleReader(_art(), articles=[_art()], article_index=0)
        assert not r.prev_article()

    def test_restore_position(self) -> None:
        content = "\n".join(f"L{i}" for i in range(100))
        r = ArticleReader(_art(content_text=content, read_position=0.5))
        assert r.scroll_offset > 0

    def test_empty_content(self) -> None:
        with patch("code_review_agent.news.content.fetch_article_content", return_value=("", "")):
            r = ArticleReader(_art(summary="", content_text=""))
        assert len(r.content_lines) >= 1
