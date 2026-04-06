"""Tests for news deduplication."""

from __future__ import annotations

from datetime import datetime

from code_review_agent.news.dedupe import (
    _char_trigrams,
    _normalize_for_comparison,
    deduplicate_within,
    hybrid_similarity,
    link_cross_source,
    token_jaccard,
    trigram_jaccard,
)
from code_review_agent.news.scoring import ScoredItem
from code_review_agent.news.sources import RawNewsItem


def _make_scored(
    title: str,
    source: str = "hackernews",
    overall: int = 50,
) -> ScoredItem:
    item = RawNewsItem(
        source=source,
        external_id="1",
        title=title,
        url="https://example.com",
        published_at=datetime.now(),
    )
    return ScoredItem(
        item=item,
        relevance=0.5,
        recency=50.0,
        engagement=50.0,
        overall=overall,
        why_relevant=f"test score={overall}",
    )


class TestTrigramJaccard:
    def test_identical_strings(self) -> None:
        assert trigram_jaccard("hello world", "hello world") == 1.0

    def test_completely_different(self) -> None:
        assert trigram_jaccard("abc", "xyz") == 0.0

    def test_similar_strings(self) -> None:
        sim = trigram_jaccard("Claude Code review", "Claude Code reviews")
        assert sim > 0.7

    def test_empty_string(self) -> None:
        assert trigram_jaccard("", "hello") == 0.0
        assert trigram_jaccard("hello", "") == 0.0

    def test_short_strings(self) -> None:
        # Less than 3 chars
        sim = trigram_jaccard("ab", "ab")
        assert sim > 0.0


class TestTokenJaccard:
    def test_with_stopwords_filtered(self) -> None:
        # "the" is a stopword, should be filtered
        sim = token_jaccard("the rust language", "rust programming language")
        assert sim > 0.3

    def test_no_common_tokens(self) -> None:
        assert token_jaccard("python testing", "rust systems") == 0.0

    def test_identical(self) -> None:
        assert token_jaccard("hello world", "hello world") == 1.0


class TestHybridSimilarity:
    def test_uses_max_of_trigram_and_token(self) -> None:
        sim = hybrid_similarity("Rust async guide", "Rust async guide 2024")
        tri = trigram_jaccard("Rust async guide", "Rust async guide 2024")
        tok = token_jaccard("Rust async guide", "Rust async guide 2024")
        assert sim == max(tri, tok)


class TestDeduplicateWithin:
    def test_removes_lower_scored_duplicate(self) -> None:
        items = [
            _make_scored("Claude Code review tips", overall=80),
            _make_scored("Claude Code review tips and tricks", overall=60),
        ]
        result = deduplicate_within(items)
        assert len(result) == 1
        assert result[0].overall == 80

    def test_keeps_unique_items(self) -> None:
        items = [
            _make_scored("Rust async patterns", overall=80),
            _make_scored("Python testing guide", overall=70),
        ]
        result = deduplicate_within(items)
        assert len(result) == 2

    def test_threshold_boundary(self) -> None:
        # Very different titles should NOT be merged
        items = [
            _make_scored("Machine learning in healthcare", overall=80),
            _make_scored("JavaScript framework comparison", overall=70),
        ]
        result = deduplicate_within(items)
        assert len(result) == 2

    def test_empty_input(self) -> None:
        assert deduplicate_within([]) == []

    def test_single_item(self) -> None:
        items = [_make_scored("Single item")]
        assert len(deduplicate_within(items)) == 1


class TestLinkCrossSource:
    def test_annotates_cross_refs(self) -> None:
        items = [
            _make_scored("Rust 2.0 released today", source="hackernews", overall=80),
            _make_scored("Rust 2.0 released today", source="reddit", overall=70),
        ]
        result = link_cross_source(items)
        assert "also on:" in result[0].why_relevant
        assert "also on:" in result[1].why_relevant

    def test_no_cross_refs_for_different_items(self) -> None:
        items = [
            _make_scored("Rust patterns", source="hackernews"),
            _make_scored("Python testing", source="reddit"),
        ]
        result = link_cross_source(items)
        assert "also on:" not in result[0].why_relevant

    def test_same_source_not_linked(self) -> None:
        items = [
            _make_scored("Similar title A", source="hackernews"),
            _make_scored("Similar title A version", source="hackernews"),
        ]
        result = link_cross_source(items)
        # Same source = no cross-link even if similar
        assert all("also on:" not in r.why_relevant for r in result)


class TestNormalizeForComparison:
    def test_strips_show_hn_prefix(self) -> None:
        result = _normalize_for_comparison("Show HN: My cool project", "hackernews")
        assert not result.startswith("show hn:")
        assert "my cool project" in result

    def test_truncates_social_posts(self) -> None:
        long_text = "A" * 200
        result = _normalize_for_comparison(long_text, "reddit")
        assert len(result) <= 100

    def test_lowercases(self) -> None:
        result = _normalize_for_comparison("Hello World", "hackernews")
        assert result == "hello world"


class TestCharTrigrams:
    def test_basic(self) -> None:
        tg = _char_trigrams("hello")
        assert "hel" in tg
        assert "ell" in tg
        assert "llo" in tg

    def test_empty(self) -> None:
        assert _char_trigrams("") == set()

    def test_short(self) -> None:
        assert len(_char_trigrams("ab")) == 1  # {"ab"}
