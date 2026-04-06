"""Tests for remaining plan items: source diversity, depth profiles, cache."""

from __future__ import annotations

from datetime import datetime, timedelta

from code_review_agent.news.scoring import (
    ScoredItem,
    interleave_sources,
)
from code_review_agent.news.sources import RawNewsItem


def _scored(source: str, score: int, ext_id: str = "1") -> ScoredItem:
    item = RawNewsItem(
        source=source,
        external_id=ext_id,
        title=f"{source} article",
        url="https://example.com",
        published_at=datetime.now(),
    )
    return ScoredItem(
        item=item,
        relevance=0.5,
        recency=50.0,
        engagement=50.0,
        overall=score,
        why_relevant="test",
    )


class TestSourceDiversity:
    """BF-2.3/2.4: Source-balanced interleaving."""

    def test_single_source_unchanged(self) -> None:
        items = [_scored("reddit", 90), _scored("reddit", 80)]
        result = interleave_sources(items)
        assert len(result) == 2

    def test_guarantees_min_per_source(self) -> None:
        items = [
            _scored("reddit", 95, "r1"),
            _scored("reddit", 90, "r2"),
            _scored("reddit", 85, "r3"),
            _scored("reddit", 80, "r4"),
            _scored("reddit", 75, "r5"),
            _scored("hackernews", 50, "h1"),
            _scored("hackernews", 45, "h2"),
            _scored("hackernews", 40, "h3"),
        ]
        result = interleave_sources(items)
        # HN items should appear in top results despite lower scores
        top_8_sources = [r.item.source for r in result[:8]]
        assert "hackernews" in top_8_sources
        hn_count = sum(1 for s in top_8_sources if s == "hackernews")
        assert hn_count >= 3  # min 3 guaranteed

    def test_three_sources_all_represented(self) -> None:
        items = [
            _scored("reddit", 90, "r1"),
            _scored("hackernews", 50, "h1"),
            _scored("web", 30, "w1"),
        ]
        result = interleave_sources(items)
        sources = {r.item.source for r in result}
        assert sources == {"reddit", "hackernews", "web"}

    def test_empty_input(self) -> None:
        assert interleave_sources([]) == []


class TestDepthProfiles:
    """FR-02.8: Depth profile configuration."""

    def test_profiles_exist(self) -> None:
        from code_review_agent.news.background import DEPTH_PROFILES

        assert "quick" in DEPTH_PROFILES
        assert "default" in DEPTH_PROFILES
        assert "deep" in DEPTH_PROFILES

    def test_quick_faster_timeout(self) -> None:
        from code_review_agent.news.background import DEPTH_PROFILES

        assert DEPTH_PROFILES["quick"]["timeout"] < DEPTH_PROFILES["default"]["timeout"]
        assert DEPTH_PROFILES["default"]["timeout"] < DEPTH_PROFILES["deep"]["timeout"]

    def test_background_accepts_depth(self) -> None:
        from unittest.mock import MagicMock

        from code_review_agent.news.background import BackgroundNewsFetch

        session = MagicMock()
        bg = BackgroundNewsFetch(domain="test", session=session, depth="deep")
        assert bg._depth == "deep"
        assert bg._profile["timeout"] == 60


class TestCacheLayer:
    """Sprint 3: 24h cache check."""

    def test_cache_returns_none_when_empty(self) -> None:
        from unittest.mock import MagicMock

        from code_review_agent.news.background import BackgroundNewsFetch

        session = MagicMock()
        bg = BackgroundNewsFetch(domain="test", session=session)

        mock_store = MagicMock()
        mock_store.load_articles.return_value = []
        assert bg._check_cache(mock_store) is None

    def test_cache_returns_articles_when_fresh(self) -> None:
        from unittest.mock import MagicMock

        from code_review_agent.news.background import BackgroundNewsFetch
        from code_review_agent.news.models import Article

        session = MagicMock()
        bg = BackgroundNewsFetch(domain="test", session=session)

        fresh_articles = [
            Article(
                id=f"hn:{i}",
                domain="hackernews",
                title=f"A{i}",
                url="https://example.com",
                fetched_at=datetime.now(),
            )
            for i in range(10)
        ]
        mock_store = MagicMock()
        mock_store.load_articles.return_value = fresh_articles
        result = bg._check_cache(mock_store)
        assert result is not None
        assert len(result) >= 5

    def test_cache_returns_none_when_stale(self) -> None:
        from unittest.mock import MagicMock

        from code_review_agent.news.background import BackgroundNewsFetch
        from code_review_agent.news.models import Article

        session = MagicMock()
        bg = BackgroundNewsFetch(domain="test", session=session)

        stale_articles = [
            Article(
                id=f"hn:{i}",
                domain="hackernews",
                title=f"A{i}",
                url="https://example.com",
                fetched_at=datetime.now() - timedelta(hours=48),
            )
            for i in range(10)
        ]
        mock_store = MagicMock()
        mock_store.load_articles.return_value = stale_articles
        result = bg._check_cache(mock_store)
        assert result is None


class TestSourceStatusDisplay:
    """Sprint 3: Quality nudge in navigator."""

    def test_viewer_accepts_source_status(self) -> None:
        from code_review_agent.news.navigator import NewsViewer

        status = {"hackernews": "ok (30)", "reddit": "ok (25)", "web": "failed: timeout"}
        viewer = NewsViewer([], source_status=status)
        assert viewer.source_status == status

    def test_viewer_default_no_status(self) -> None:
        from code_review_agent.news.navigator import NewsViewer

        viewer = NewsViewer([])
        assert viewer.source_status == {}
