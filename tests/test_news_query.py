"""Tests for news query preprocessor."""

from __future__ import annotations

from code_review_agent.news.query import (
    ProcessedQuery,
    _detect_compounds,
    _filter_noise,
    _strip_prefix,
    preprocess_query,
)


class TestStripPrefix:
    def test_strips_what_are_the_best(self) -> None:
        assert _strip_prefix("what are the best AI tools") == "AI tools"

    def test_strips_how_to_use(self) -> None:
        assert _strip_prefix("how to use Claude Code") == "Claude Code"

    def test_strips_tips_for(self) -> None:
        assert _strip_prefix("tips for Python testing") == "Python testing"

    def test_preserves_non_prefix(self) -> None:
        assert _strip_prefix("Claude Code review") == "Claude Code review"

    def test_strips_longest_prefix_first(self) -> None:
        # "what are the best" is longer than "what are"
        assert _strip_prefix("what are the best tools") == "tools"

    def test_empty_after_strip_returns_original(self) -> None:
        assert _strip_prefix("how to") == "how to"


class TestDetectCompounds:
    def test_title_case_quoted(self) -> None:
        compounds = _detect_compounds("I love Claude Code for reviews")
        assert "Claude Code" in compounds

    def test_hyphenated_preserved(self) -> None:
        compounds = _detect_compounds("multi-agent systems are great")
        assert "multi-agent" in compounds

    def test_single_words_not_quoted(self) -> None:
        compounds = _detect_compounds("python testing")
        assert compounds == []

    def test_multiple_compounds(self) -> None:
        compounds = _detect_compounds("Claude Code and Hacker News")
        assert "Claude Code" in compounds
        assert "Hacker News" in compounds


class TestFilterNoise:
    def test_removes_articles(self) -> None:
        assert _filter_noise(["the", "a", "code"]) == ["code"]

    def test_removes_filler(self) -> None:
        assert _filter_noise(["really", "very", "good", "rust"]) == ["rust"]

    def test_preserves_meaningful_words(self) -> None:
        assert _filter_noise(["claude", "code", "review"]) == ["claude", "code", "review"]

    def test_all_noise_returns_empty(self) -> None:
        assert _filter_noise(["the", "best", "how", "to"]) == []

    def test_removes_short_words(self) -> None:
        assert _filter_noise(["a", "i", "claude"]) == ["claude"]


class TestPreprocessQuery:
    def test_full_pipeline(self) -> None:
        q = preprocess_query("what are the best Claude Code tips")
        assert "claude" in q.core_terms
        assert "code" in q.core_terms
        assert "Claude Code" in q.quoted_phrases

    def test_empty_query(self) -> None:
        q = preprocess_query("")
        assert q.core_terms == ()
        assert q.quoted_phrases == ()
        assert q.hn_query == ""

    def test_noise_only_query(self) -> None:
        q = preprocess_query("the best how to")
        # Should recover something rather than be totally empty
        assert isinstance(q, ProcessedQuery)

    def test_query_is_frozen(self) -> None:
        import pytest

        q = preprocess_query("test")
        with pytest.raises(AttributeError):
            q.raw = "changed"  # type: ignore[misc]

    def test_per_source_queries_populated(self) -> None:
        q = preprocess_query("rust async patterns")
        assert q.hn_query
        assert q.reddit_query
        assert q.web_query

    def test_web_query_quotes_compounds(self) -> None:
        q = preprocess_query("Claude Code best practices")
        assert '"Claude Code"' in q.web_query
