"""Tests for learning from dismissed findings."""

from __future__ import annotations

from unittest.mock import MagicMock

from code_review_agent.learning import (
    SuppressionPattern,
    build_suppression_prompt,
    filter_patterns_for_agent,
    load_suppression_patterns,
)


class TestLoadSuppressionPatterns:
    """Test querying dismissed patterns from storage."""

    def test_returns_patterns_from_storage(self) -> None:
        storage = MagicMock()
        storage.query_dismissed_patterns.return_value = [
            {
                "title": "unused import",
                "agent_name": "style",
                "dismiss_count": 3,
                "triage_action": "false_positive",
            },
        ]
        patterns = load_suppression_patterns(storage)
        assert len(patterns) == 1
        assert patterns[0].title == "unused import"
        assert patterns[0].agent_name == "style"
        assert patterns[0].dismiss_count == 3

    def test_returns_empty_on_error(self) -> None:
        storage = MagicMock()
        storage.query_dismissed_patterns.side_effect = RuntimeError("db error")
        patterns = load_suppression_patterns(storage)
        assert patterns == []

    def test_respects_min_count(self) -> None:
        storage = MagicMock()
        storage.query_dismissed_patterns.return_value = []
        load_suppression_patterns(storage, min_count=5)
        storage.query_dismissed_patterns.assert_called_once_with(min_count=5)


class TestBuildSuppressionPrompt:
    """Test building agent prompt from suppression patterns."""

    def test_empty_patterns(self) -> None:
        assert build_suppression_prompt([]) == ""

    def test_formats_patterns(self) -> None:
        patterns = [
            SuppressionPattern("unused import", "style", 3, "false_positive"),
            SuppressionPattern("too many args", "style", 2, "ignored"),
        ]
        prompt = build_suppression_prompt(patterns)
        assert "Previously dismissed" in prompt
        assert "unused import" in prompt
        assert "3x" in prompt
        assert "too many args" in prompt


class TestFilterPatternsForAgent:
    """Test filtering patterns by agent name."""

    def test_filters_by_agent(self) -> None:
        patterns = [
            SuppressionPattern("sql injection", "security", 2, "false_positive"),
            SuppressionPattern("unused import", "style", 3, "false_positive"),
            SuppressionPattern("n+1 query", "performance", 2, "ignored"),
        ]
        security_patterns = filter_patterns_for_agent(patterns, "security")
        assert len(security_patterns) == 1
        assert security_patterns[0].title == "sql injection"

    def test_empty_when_no_match(self) -> None:
        patterns = [
            SuppressionPattern("unused import", "style", 3, "false_positive"),
        ]
        assert filter_patterns_for_agent(patterns, "security") == []

    def test_pattern_is_frozen(self) -> None:
        import pytest

        p = SuppressionPattern("test", "agent", 2, "false_positive")
        with pytest.raises(AttributeError):
            p.title = "changed"  # type: ignore[misc]
