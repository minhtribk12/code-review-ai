"""Tests for news scoring engine."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from code_review_agent.news.query import preprocess_query
from code_review_agent.news.scoring import (
    DEFAULT_ENGAGEMENT,
    engagement_score_hn,
    engagement_score_reddit,
    normalize_engagement,
    recency_score,
    score_all,
    score_item,
    token_overlap_relevance,
)
from code_review_agent.news.sources import RawNewsItem


def _make_item(
    title: str = "Test",
    source: str = "hackernews",
    score: int = 50,
    comments: int = 10,
    published_at: datetime | None = None,
    summary: str = "",
) -> RawNewsItem:
    return RawNewsItem(
        source=source,
        external_id="1",
        title=title,
        url="https://example.com",
        score=score,
        comment_count=comments,
        published_at=published_at or datetime.now(),
        summary=summary,
    )


class TestTokenOverlapRelevance:
    def test_full_overlap_scores_high(self) -> None:
        score = token_overlap_relevance(("rust", "async"), "Rust async patterns explained")
        assert score > 0.7

    def test_no_overlap_scores_zero(self) -> None:
        score = token_overlap_relevance(("python", "testing"), "JavaScript framework comparison")
        assert score < 0.1

    def test_partial_overlap(self) -> None:
        score = token_overlap_relevance(("rust", "async", "patterns"), "Rust memory safety")
        assert 0.1 < score < 0.7

    def test_coverage_exponent_applied(self) -> None:
        # coverage^1.35 should boost high coverage more than linear
        full = token_overlap_relevance(("rust",), "Rust programming")
        assert full > 0.5

    def test_phrase_bonus_multi_word(self) -> None:
        without = token_overlap_relevance(("claude", "code"), "code by claude user")
        with_phrase = token_overlap_relevance(("claude", "code"), "Claude Code is great")
        assert with_phrase > without  # phrase bonus +0.12

    def test_phrase_bonus_single_word(self) -> None:
        score = token_overlap_relevance(("rust",), "Rust is fast")
        assert score > 0.6  # single-word phrase bonus +0.16

    def test_generic_penalty_caps_at_024(self) -> None:
        # Query with only low-signal tokens
        score = token_overlap_relevance(("best", "tips"), "best tips for coding")
        assert score <= 0.24

    def test_stopword_filtering(self) -> None:
        # "the" and "is" are stopwords, should not count
        score = token_overlap_relevance(("the", "is", "rust"), "Rust programming")
        assert score > 0.3  # "rust" matches

    def test_synonym_expansion(self) -> None:
        score = token_overlap_relevance(("js",), "JavaScript framework comparison")
        assert score > 0.3  # js -> javascript synonym

    def test_empty_query_returns_05(self) -> None:
        assert token_overlap_relevance((), "some text") == 0.5

    def test_empty_text(self) -> None:
        score = token_overlap_relevance(("rust",), "")
        assert score < 0.3


class TestEngagementScore:
    def test_hn_formula(self) -> None:
        score = engagement_score_hn(points=100, comments=50)
        expected = 0.55 * math.log1p(100) + 0.45 * math.log1p(50)
        assert abs(score - expected) < 0.01

    def test_reddit_formula(self) -> None:
        score = engagement_score_reddit(score=500, comments=100)
        assert score > 0

    def test_zero_engagement(self) -> None:
        assert engagement_score_hn(0, 0) == 0.0
        assert engagement_score_reddit(0, 0) == 0.0


class TestRecencyScore:
    def test_very_recent(self) -> None:
        score = recency_score(datetime.now() - timedelta(hours=1))
        assert score == 100.0

    def test_one_day_old(self) -> None:
        score = recency_score(datetime.now() - timedelta(hours=24))
        assert 70 < score < 95

    def test_one_week_old(self) -> None:
        score = recency_score(datetime.now() - timedelta(days=7))
        assert 40 < score < 70

    def test_none_date(self) -> None:
        assert recency_score(None) == 50.0


class TestNormalizeEngagement:
    def test_min_max_scaling(self) -> None:
        result = normalize_engagement([0.0, 5.0, 10.0])
        assert result[0] == 0.0
        assert result[-1] == 100.0

    def test_single_item(self) -> None:
        result = normalize_engagement([5.0])
        assert result == [DEFAULT_ENGAGEMENT]

    def test_identical_values(self) -> None:
        result = normalize_engagement([5.0, 5.0, 5.0])
        assert all(v == DEFAULT_ENGAGEMENT for v in result)

    def test_empty(self) -> None:
        assert normalize_engagement([]) == []


class TestScoreItem:
    def test_three_component_weights(self) -> None:
        query = preprocess_query("rust async")
        item = _make_item("Rust async patterns", score=100, comments=50)
        scored = score_item(query, item, normalized_eng=80.0)
        assert 0 <= scored.overall <= 100
        assert scored.relevance > 0
        assert scored.why_relevant

    def test_date_penalty_low_confidence(self) -> None:
        query = preprocess_query("test")
        item = RawNewsItem(
            source="hackernews",
            external_id="1",
            title="Test article",
            url="https://example.com",
            date_confidence="low",
            published_at=datetime.now(),
        )
        scored_low = score_item(query, item, normalized_eng=50.0)
        scored_high = score_item(query, _make_item("Test article"), normalized_eng=50.0)
        # Low confidence should have lower overall (penalty of -5)
        assert scored_low.overall <= scored_high.overall

    def test_score_clamped_0_100(self) -> None:
        query = preprocess_query("test")
        item = _make_item("Completely unrelated xyz abc")
        scored = score_item(query, item, normalized_eng=0.0)
        assert 0 <= scored.overall <= 100


class TestScoreAll:
    def test_sorted_by_overall(self) -> None:
        query = preprocess_query("rust")
        items = [
            _make_item("Python testing", score=10),
            _make_item("Rust programming guide", score=100),
        ]
        scored = score_all(query, items)
        assert scored[0].overall >= scored[-1].overall

    def test_minimum_guarantee(self) -> None:
        query = preprocess_query("xyznonexistent")
        items = [_make_item(f"Unrelated item {i}") for i in range(5)]
        scored = score_all(query, items)
        assert len(scored) >= 3

    def test_empty_items(self) -> None:
        query = preprocess_query("test")
        assert score_all(query, []) == []

    def test_scored_item_is_frozen(self) -> None:
        import pytest

        query = preprocess_query("test")
        items = [_make_item("Test")]
        scored = score_all(query, items)
        with pytest.raises(AttributeError):
            scored[0].overall = 999  # type: ignore[misc]
