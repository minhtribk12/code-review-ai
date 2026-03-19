"""Tests for AgentDefinition model and resolve_all_agents."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from code_review_agent.agent_definition import AgentDefinition, AgentSource
from code_review_agent.storage import ReviewStorage

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def storage(tmp_path: Path) -> ReviewStorage:
    return ReviewStorage(str(tmp_path / "test.db"))


class TestAgentDefinition:
    def test_valid_creation(self) -> None:
        agent = AgentDefinition(
            name="my_agent",
            system_prompt="You review code.",
            description="Custom agent",
            priority=50,
            source=AgentSource.DB,
        )
        assert agent.name == "my_agent"
        assert agent.priority == 50
        assert agent.source == AgentSource.DB

    def test_frozen(self) -> None:
        agent = AgentDefinition(name="test_agent", system_prompt="P")
        with pytest.raises(ValidationError):
            agent.name = "changed"  # type: ignore[misc]

    def test_invalid_name_pattern(self) -> None:
        with pytest.raises(ValidationError):
            AgentDefinition(name="Invalid-Name", system_prompt="P")

    def test_name_must_start_with_letter(self) -> None:
        with pytest.raises(ValidationError):
            AgentDefinition(name="123agent", system_prompt="P")

    def test_empty_system_prompt_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AgentDefinition(name="empty_prompt", system_prompt="")

    def test_negative_priority_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AgentDefinition(name="neg_pri", system_prompt="P", priority=-1)

    def test_defaults(self) -> None:
        agent = AgentDefinition(name="defaults", system_prompt="P")
        assert agent.description == ""
        assert agent.priority == 100
        assert agent.enabled is True
        assert agent.file_patterns is None
        assert agent.source == AgentSource.BUILTIN

    def test_file_patterns(self) -> None:
        agent = AgentDefinition(
            name="pat_agent",
            system_prompt="P",
            file_patterns=["*.py", "*.js"],
        )
        assert agent.file_patterns == ["*.py", "*.js"]


class TestAgentSource:
    def test_values(self) -> None:
        assert AgentSource.BUILTIN == "built-in"
        assert AgentSource.YAML == "yaml"
        assert AgentSource.DB == "db"


class TestResolveAllAgents:
    def test_db_overrides_builtin(self, storage: ReviewStorage) -> None:
        """DB agents should appear with source=db, overriding built-in."""
        storage.save_agent(
            name="security",
            system_prompt="Custom security prompt",
            description="Overridden",
            priority=0,
        )
        agents = storage.load_all_agents()
        assert len(agents) == 1
        assert agents[0]["name"] == "security"
        assert agents[0]["system_prompt"] == "Custom security prompt"

    def test_db_agent_new_name(self, storage: ReviewStorage) -> None:
        """DB agents with unique names are added to the list."""
        storage.save_agent(
            name="custom_lint",
            system_prompt="You check linting.",
            priority=50,
        )
        agents = storage.load_all_agents()
        assert any(a["name"] == "custom_lint" for a in agents)

    def test_disabled_db_agent_in_list(self, storage: ReviewStorage) -> None:
        """Disabled agents are loaded (visibility is controlled by consumer)."""
        storage.save_agent(
            name="disabled_agent",
            system_prompt="P",
            enabled=False,
        )
        agents = storage.load_all_agents()
        disabled = [a for a in agents if a["name"] == "disabled_agent"]
        assert len(disabled) == 1
        assert disabled[0]["enabled"] is False

    def test_ordering(self, storage: ReviewStorage) -> None:
        storage.save_agent(name="z_high", system_prompt="P", priority=200)
        storage.save_agent(name="a_low", system_prompt="P", priority=1)
        storage.save_agent(name="m_mid", system_prompt="P", priority=50)

        agents = storage.load_all_agents()
        names = [a["name"] for a in agents]
        assert names == ["a_low", "m_mid", "z_high"]
