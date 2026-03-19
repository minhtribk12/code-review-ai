"""Tests for custom agent CRUD in ReviewStorage."""

from __future__ import annotations

import json

import pytest

from code_review_agent.storage import ReviewStorage


@pytest.fixture
def storage(tmp_path: object) -> ReviewStorage:
    """Create a storage instance with a temp database."""
    from pathlib import Path

    db_path = Path(str(tmp_path)) / "test.db"
    return ReviewStorage(str(db_path))


class TestSaveAndLoadAgent:
    def test_round_trip(self, storage: ReviewStorage) -> None:
        storage.save_agent(
            name="my_agent",
            system_prompt="You are a test agent.",
            description="Test description",
            priority=50,
            enabled=True,
            file_patterns=["*.py", "*.js"],
        )
        agent = storage.load_agent("my_agent")
        assert agent is not None
        assert agent["name"] == "my_agent"
        assert agent["system_prompt"] == "You are a test agent."
        assert agent["description"] == "Test description"
        assert agent["priority"] == 50
        assert agent["enabled"] is True
        assert agent["file_patterns"] == ["*.py", "*.js"]

    def test_load_nonexistent(self, storage: ReviewStorage) -> None:
        assert storage.load_agent("nonexistent") is None

    def test_save_with_none_patterns(self, storage: ReviewStorage) -> None:
        storage.save_agent(
            name="no_patterns",
            system_prompt="Prompt text.",
            file_patterns=None,
        )
        agent = storage.load_agent("no_patterns")
        assert agent is not None
        assert agent["file_patterns"] is None

    def test_save_with_empty_patterns_list(self, storage: ReviewStorage) -> None:
        storage.save_agent(
            name="empty_patterns",
            system_prompt="Prompt text.",
            file_patterns=[],
        )
        agent = storage.load_agent("empty_patterns")
        assert agent is not None
        assert agent["file_patterns"] == []

    def test_defaults(self, storage: ReviewStorage) -> None:
        storage.save_agent(name="minimal", system_prompt="Hello.")
        agent = storage.load_agent("minimal")
        assert agent is not None
        assert agent["description"] == ""
        assert agent["priority"] == 100
        assert agent["enabled"] is True
        assert agent["file_patterns"] is None


class TestUpsert:
    def test_update_existing(self, storage: ReviewStorage) -> None:
        storage.save_agent(name="agent_a", system_prompt="V1")
        storage.save_agent(name="agent_a", system_prompt="V2", priority=10)
        agent = storage.load_agent("agent_a")
        assert agent is not None
        assert agent["system_prompt"] == "V2"
        assert agent["priority"] == 10

    def test_upsert_preserves_created_at(self, storage: ReviewStorage) -> None:
        storage.save_agent(name="agent_b", system_prompt="First")
        first = storage.load_agent("agent_b")
        assert first is not None
        created_at = first["created_at"]

        storage.save_agent(name="agent_b", system_prompt="Second")
        second = storage.load_agent("agent_b")
        assert second is not None
        assert second["created_at"] == created_at


class TestLoadAllAgents:
    def test_empty(self, storage: ReviewStorage) -> None:
        assert storage.load_all_agents() == []

    def test_ordered_by_priority_then_name(self, storage: ReviewStorage) -> None:
        storage.save_agent(name="z_agent", system_prompt="P", priority=10)
        storage.save_agent(name="a_agent", system_prompt="P", priority=10)
        storage.save_agent(name="m_agent", system_prompt="P", priority=5)

        agents = storage.load_all_agents()
        names = [a["name"] for a in agents]
        assert names == ["m_agent", "a_agent", "z_agent"]

    def test_includes_disabled(self, storage: ReviewStorage) -> None:
        storage.save_agent(name="enabled_one", system_prompt="P", enabled=True)
        storage.save_agent(name="disabled_one", system_prompt="P", enabled=False)

        agents = storage.load_all_agents()
        assert len(agents) == 2
        disabled = [a for a in agents if a["name"] == "disabled_one"]
        assert disabled[0]["enabled"] is False


class TestDeleteAgent:
    def test_delete_existing(self, storage: ReviewStorage) -> None:
        storage.save_agent(name="to_delete", system_prompt="P")
        assert storage.delete_agent("to_delete") is True
        assert storage.load_agent("to_delete") is None

    def test_delete_nonexistent(self, storage: ReviewStorage) -> None:
        assert storage.delete_agent("nonexistent") is False


class TestFilePatternsSerialization:
    def test_json_round_trip(self, storage: ReviewStorage) -> None:
        patterns = ["*.py", "src/**/*.ts", "!test_*.py"]
        storage.save_agent(name="pat_agent", system_prompt="P", file_patterns=patterns)
        agent = storage.load_agent("pat_agent")
        assert agent is not None
        assert agent["file_patterns"] == patterns

    def test_null_pattern(self, storage: ReviewStorage) -> None:
        storage.save_agent(name="null_pat", system_prompt="P", file_patterns=None)
        agent = storage.load_agent("null_pat")
        assert agent is not None
        assert agent["file_patterns"] is None

    def test_raw_json_in_db(self, storage: ReviewStorage) -> None:
        """Verify the DB stores JSON, not Python repr."""
        patterns = ["*.py"]
        storage.save_agent(name="json_check", system_prompt="P", file_patterns=patterns)
        with storage._get_connection() as conn:
            row = conn.execute(
                "SELECT file_patterns FROM custom_agents WHERE name = ?",
                ("json_check",),
            ).fetchone()
        assert row is not None
        assert json.loads(row["file_patterns"]) == patterns
