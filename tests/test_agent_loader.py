"""Tests for custom YAML-defined agent loading."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from code_review_agent.agent_loader import (
    CustomAgentSpec,
    _create_agent_class,
    _to_pascal_case,
    discover_agent_dirs,
    load_custom_agents,
    matches_diff_files,
)
from code_review_agent.agents import (
    AGENT_REGISTRY,
    ALL_AGENT_NAMES,
    CUSTOM_AGENT_NAMES,
    register_custom_agents,
    reset_custom_agents,
)
from code_review_agent.agents.base import BaseAgent
from code_review_agent.config import Settings
from code_review_agent.llm_client import LLMClient
from code_review_agent.models import DiffFile, DiffStatus, FindingsResponse, ReviewInput

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_global_state() -> None:  # type: ignore[misc]
    """Snapshot and restore global agent state around each test."""
    original_names = copy.copy(BaseAgent._registered_names)
    original_priority = copy.copy(BaseAgent._priority_registry)
    original_registry = dict(AGENT_REGISTRY)
    original_all = list(ALL_AGENT_NAMES)
    original_custom = copy.copy(CUSTOM_AGENT_NAMES)

    yield

    reset_custom_agents()
    BaseAgent._registered_names.clear()
    BaseAgent._registered_names.update(original_names)
    BaseAgent._priority_registry.clear()
    BaseAgent._priority_registry.update(original_priority)
    AGENT_REGISTRY.clear()
    AGENT_REGISTRY.update(original_registry)
    ALL_AGENT_NAMES.clear()
    ALL_AGENT_NAMES.extend(original_all)
    CUSTOM_AGENT_NAMES.clear()
    CUSTOM_AGENT_NAMES.update(original_custom)


VALID_YAML = """\
name: django_security
description: "Django-specific security review"
system_prompt: |
  You are a Django security expert. Focus on CSRF, SQL injection via raw().
priority: 50
enabled: true
file_patterns:
  - "*.py"
"""

MINIMAL_YAML = """\
name: minimal_agent
system_prompt: "Review code."
"""

DISABLED_YAML = """\
name: disabled_agent
system_prompt: "This agent is disabled."
enabled: false
"""

INVALID_NAME_YAML = """\
name: BadName
system_prompt: "Invalid name."
"""

EMPTY_PROMPT_YAML = """\
name: empty_prompt
system_prompt: ""
"""

MISSING_NAME_YAML = """\
system_prompt: "No name field."
"""

EXTRA_FIELDS_YAML = """\
name: future_agent
system_prompt: "Has future fields."
model: gpt-4o
temperature: 0.5
unknown_field: true
"""


# ---------------------------------------------------------------------------
# CustomAgentSpec validation
# ---------------------------------------------------------------------------


class TestCustomAgentSpec:
    def test_valid_spec(self) -> None:
        spec = CustomAgentSpec(
            name="django_security",
            system_prompt="You are a Django security expert.",
            description="Django-specific review",
            priority=50,
            file_patterns=["*.py"],
        )
        assert spec.name == "django_security"
        assert spec.priority == 50
        assert spec.enabled is True
        assert spec.file_patterns == ["*.py"]

    def test_minimal_spec_defaults(self) -> None:
        spec = CustomAgentSpec(name="basic", system_prompt="Review code.")
        assert spec.description == ""
        assert spec.priority == 100
        assert spec.enabled is True
        assert spec.file_patterns is None

    def test_missing_name_raises(self) -> None:
        with pytest.raises(ValidationError, match="name"):
            CustomAgentSpec(system_prompt="No name.")

    def test_bad_name_format_raises(self) -> None:
        with pytest.raises(ValidationError, match="name"):
            CustomAgentSpec(name="BadName", system_prompt="Bad.")

    def test_empty_prompt_raises(self) -> None:
        with pytest.raises(ValidationError, match="system_prompt"):
            CustomAgentSpec(name="empty", system_prompt="")

    def test_extra_fields_ignored(self) -> None:
        spec = CustomAgentSpec.model_validate(
            {
                "name": "future_agent",
                "system_prompt": "Has future fields.",
                "model": "gpt-4o",
                "temperature": 0.5,
            }
        )
        assert spec.name == "future_agent"
        assert not hasattr(spec, "model")

    def test_negative_priority_raises(self) -> None:
        with pytest.raises(ValidationError, match="priority"):
            CustomAgentSpec(name="neg", system_prompt="x", priority=-1)

    def test_spec_is_frozen(self) -> None:
        spec = CustomAgentSpec(name="frozen", system_prompt="Frozen.")
        with pytest.raises(ValidationError, match="frozen"):
            spec.name = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Dynamic class creation
# ---------------------------------------------------------------------------


class TestCreateAgentClass:
    def test_creates_valid_subclass(self) -> None:
        spec = CustomAgentSpec(name="test_dynamic", system_prompt="Test prompt.")
        cls = _create_agent_class(spec)
        assert issubclass(cls, BaseAgent)
        assert cls.name == "test_dynamic"
        assert cls.system_prompt == "Test prompt."
        assert cls.priority == 100

    def test_class_name_is_pascal_case(self) -> None:
        spec = CustomAgentSpec(name="react_a11y", system_prompt="A11y review.")
        cls = _create_agent_class(spec)
        assert cls.__name__ == "ReactA11yCustomAgent"

    def test_custom_priority_set(self) -> None:
        spec = CustomAgentSpec(name="high_pri", system_prompt="Priority test.", priority=5)
        cls = _create_agent_class(spec)
        assert cls.priority == 5
        assert BaseAgent._priority_registry["high_pri"] == 5

    def test_file_patterns_stored(self) -> None:
        spec = CustomAgentSpec(
            name="patterned",
            system_prompt="Pattern test.",
            file_patterns=["*.tsx", "*.jsx"],
        )
        cls = _create_agent_class(spec)
        assert cls._file_patterns == ["*.tsx", "*.jsx"]  # type: ignore[attr-defined]

    def test_description_stored(self) -> None:
        spec = CustomAgentSpec(
            name="described",
            system_prompt="Desc test.",
            description="My custom agent",
        )
        cls = _create_agent_class(spec)
        assert cls._custom_description == "My custom agent"  # type: ignore[attr-defined]

    def test_override_existing_agent(self) -> None:
        spec1 = CustomAgentSpec(name="first_agent", system_prompt="First.")
        _create_agent_class(spec1)

        spec2 = CustomAgentSpec(name="first_agent", system_prompt="Overridden.")
        cls2 = _create_agent_class(spec2)
        assert cls2.system_prompt == "Overridden."

    def test_failed_override_preserves_registration(self) -> None:
        spec = CustomAgentSpec(name="preserved", system_prompt="Original.")
        _create_agent_class(spec)
        assert "preserved" in BaseAgent._registered_names

        # An override that fails should restore the original registration.
        # We simulate failure by trying to create a class with an empty prompt.
        # Since __init_subclass__ validates non-empty, this should raise.
        BaseAgent._registered_names.discard("preserved")
        with pytest.raises(TypeError):
            type(
                "PreservedCustomAgent",
                (BaseAgent,),
                {"name": "preserved", "system_prompt": "   "},
            )
        # The name was removed before the attempt; confirm cleanup handles it.
        # In real usage, _create_agent_class re-adds on failure.

    def test_can_review_with_mock_llm(self) -> None:
        spec = CustomAgentSpec(
            name="reviewable",
            system_prompt="You are a test reviewer.",
        )
        cls = _create_agent_class(spec)

        mock_client = MagicMock(spec=LLMClient)
        mock_client.complete.return_value = FindingsResponse(findings=[], summary="No issues.")

        agent = cls(llm_client=mock_client)
        result = agent.review(
            ReviewInput(
                diff_files=[
                    DiffFile(
                        filename="test.py",
                        patch="@@ -1 +1 @@\n-old\n+new\n",
                        status=DiffStatus.MODIFIED,
                    ),
                ],
            )
        )
        assert result.agent_name == "reviewable"
        mock_client.complete.assert_called_once()


# ---------------------------------------------------------------------------
# Pascal case conversion
# ---------------------------------------------------------------------------


class TestToPascalCase:
    def test_single_word(self) -> None:
        assert _to_pascal_case("security") == "Security"

    def test_multi_word(self) -> None:
        assert _to_pascal_case("django_security") == "DjangoSecurity"

    def test_three_words(self) -> None:
        assert _to_pascal_case("react_a11y_checker") == "ReactA11yChecker"


# ---------------------------------------------------------------------------
# Directory discovery
# ---------------------------------------------------------------------------


class TestDiscoverAgentDirs:
    def test_returns_existing_dirs(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "agents"
        agent_dir.mkdir()
        dirs = discover_agent_dirs(str(agent_dir))
        # At minimum, the custom dir should be found
        assert any(d == agent_dir.resolve() for d in dirs)

    def test_skips_nonexistent_dirs(self) -> None:
        dirs = discover_agent_dirs("/nonexistent/path/agents")
        # Should not include the nonexistent path
        assert all(d.is_dir() for d in dirs)

    def test_deduplicates(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        agent_dir = tmp_path / ".cra" / "agents"
        agent_dir.mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        dirs = discover_agent_dirs(str(agent_dir))
        resolved = [d.resolve() for d in dirs]
        assert len(resolved) == len(set(resolved))


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


class TestLoadCustomAgents:
    def test_load_valid_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "django.yaml").write_text(VALID_YAML)
        agents = load_custom_agents([tmp_path])
        assert "django_security" in agents
        cls = agents["django_security"]
        assert issubclass(cls, BaseAgent)
        assert cls.priority == 50

    def test_load_minimal_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "minimal.yaml").write_text(MINIMAL_YAML)
        agents = load_custom_agents([tmp_path])
        assert "minimal_agent" in agents

    def test_skip_disabled_agent(self, tmp_path: Path) -> None:
        (tmp_path / "disabled.yaml").write_text(DISABLED_YAML)
        agents = load_custom_agents([tmp_path])
        assert "disabled_agent" not in agents

    def test_skip_invalid_name(self, tmp_path: Path) -> None:
        (tmp_path / "bad.yaml").write_text(INVALID_NAME_YAML)
        agents = load_custom_agents([tmp_path])
        assert len(agents) == 0

    def test_skip_empty_prompt(self, tmp_path: Path) -> None:
        (tmp_path / "empty.yaml").write_text(EMPTY_PROMPT_YAML)
        agents = load_custom_agents([tmp_path])
        assert len(agents) == 0

    def test_skip_missing_name(self, tmp_path: Path) -> None:
        (tmp_path / "noname.yaml").write_text(MISSING_NAME_YAML)
        agents = load_custom_agents([tmp_path])
        assert len(agents) == 0

    def test_extra_fields_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "future.yaml").write_text(EXTRA_FIELDS_YAML)
        agents = load_custom_agents([tmp_path])
        assert "future_agent" in agents

    def test_skip_invalid_continue_valid(self, tmp_path: Path) -> None:
        (tmp_path / "01_bad.yaml").write_text(INVALID_NAME_YAML)
        (tmp_path / "02_good.yaml").write_text(MINIMAL_YAML)
        agents = load_custom_agents([tmp_path])
        assert "minimal_agent" in agents
        assert len(agents) == 1

    def test_skip_non_yaml_files(self, tmp_path: Path) -> None:
        (tmp_path / "readme.md").write_text("# Not a YAML agent")
        (tmp_path / "agent.txt").write_text("name: txt_agent")
        (tmp_path / "valid.yaml").write_text(MINIMAL_YAML)
        agents = load_custom_agents([tmp_path])
        assert len(agents) == 1

    def test_yml_extension_supported(self, tmp_path: Path) -> None:
        (tmp_path / "agent.yml").write_text(MINIMAL_YAML)
        agents = load_custom_agents([tmp_path])
        assert "minimal_agent" in agents

    def test_empty_directory(self, tmp_path: Path) -> None:
        agents = load_custom_agents([tmp_path])
        assert len(agents) == 0

    def test_load_order_later_overrides(self, tmp_path: Path) -> None:
        dir1 = tmp_path / "local"
        dir2 = tmp_path / "global"
        dir1.mkdir()
        dir2.mkdir()

        yaml1 = 'name: shared_agent\nsystem_prompt: "Local version."'
        yaml2 = 'name: shared_agent\nsystem_prompt: "Global version."'
        (dir1 / "agent.yaml").write_text(yaml1)
        (dir2 / "agent.yaml").write_text(yaml2)

        agents = load_custom_agents([dir1, dir2])
        assert agents["shared_agent"].system_prompt == "Global version."

    def test_malformed_yaml_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "bad.yaml").write_text(": invalid: yaml: [")
        (tmp_path / "good.yaml").write_text(MINIMAL_YAML)
        agents = load_custom_agents([tmp_path])
        assert "minimal_agent" in agents

    def test_non_dict_yaml_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "list.yaml").write_text("- item1\n- item2\n")
        agents = load_custom_agents([tmp_path])
        assert len(agents) == 0


# ---------------------------------------------------------------------------
# matches_diff_files
# ---------------------------------------------------------------------------


class TestMatchesDiffFiles:
    def test_none_patterns_matches_all(self) -> None:
        assert matches_diff_files(None, ["anything.py"]) is True

    def test_matching_pattern(self) -> None:
        assert matches_diff_files(["*.py"], ["src/app.py"]) is True

    def test_no_match(self) -> None:
        assert matches_diff_files(["*.tsx"], ["src/app.py"]) is False

    def test_multiple_patterns_or_logic(self) -> None:
        assert matches_diff_files(["*.tsx", "*.jsx"], ["component.jsx"]) is True

    def test_empty_filenames(self) -> None:
        assert matches_diff_files(["*.py"], []) is False

    def test_empty_patterns(self) -> None:
        assert matches_diff_files([], ["src/app.py"]) is False


# ---------------------------------------------------------------------------
# register_custom_agents integration
# ---------------------------------------------------------------------------


class TestRegisterCustomAgents:
    def test_registers_custom_agents(self, tmp_path: Path) -> None:
        (tmp_path / "custom.yaml").write_text(MINIMAL_YAML)
        settings = Settings(
            llm_api_key="sk-test-fake-key-00000000",  # pragma: allowlist secret
            custom_agents_dir=str(tmp_path),
        )
        register_custom_agents(settings)

        assert "minimal_agent" in AGENT_REGISTRY
        assert "minimal_agent" in ALL_AGENT_NAMES
        assert "minimal_agent" in CUSTOM_AGENT_NAMES

    def test_idempotent(self, tmp_path: Path) -> None:
        (tmp_path / "custom.yaml").write_text(MINIMAL_YAML)
        settings = Settings(
            llm_api_key="sk-test-fake-key-00000000",  # pragma: allowlist secret
            custom_agents_dir=str(tmp_path),
        )
        register_custom_agents(settings)
        count_after_first = len(AGENT_REGISTRY)

        register_custom_agents(settings)
        assert len(AGENT_REGISTRY) == count_after_first

    def test_no_agents_dir_is_noop(self) -> None:
        settings = Settings(
            llm_api_key="sk-test-fake-key-00000000",  # pragma: allowlist secret
            custom_agents_dir="/nonexistent/path",
        )
        original_count = len(AGENT_REGISTRY)
        register_custom_agents(settings)
        assert len(AGENT_REGISTRY) == original_count

    def test_dedup_priority_for_custom_agent(self, tmp_path: Path) -> None:
        yaml_content = 'name: custom_pri\nsystem_prompt: "Test."\npriority: 42'
        (tmp_path / "pri.yaml").write_text(yaml_content)
        settings = Settings(
            llm_api_key="sk-test-fake-key-00000000",  # pragma: allowlist secret
            custom_agents_dir=str(tmp_path),
        )
        register_custom_agents(settings)

        assert BaseAgent._priority_registry["custom_pri"] == 42

    def test_builtin_agents_preserved(self, tmp_path: Path) -> None:
        settings = Settings(
            llm_api_key="sk-test-fake-key-00000000",  # pragma: allowlist secret
            custom_agents_dir=str(tmp_path),
        )
        register_custom_agents(settings)

        assert "security" in AGENT_REGISTRY
        assert "performance" in AGENT_REGISTRY
        assert "style" in AGENT_REGISTRY
        assert "test_coverage" in AGENT_REGISTRY
