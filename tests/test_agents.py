from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from code_review_agent.agents.base import BaseAgent
from code_review_agent.agents.security import SecurityAgent
from code_review_agent.llm_client import (
    LLMClient,
    LLMEmptyResponseError,
    LLMResponseParseError,
)
from code_review_agent.models import (
    AgentStatus,
    DiffFile,
    Finding,
    FindingsResponse,
    ReviewInput,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def llm_client() -> MagicMock:
    """Mock LLMClient that returns a successful FindingsResponse."""
    client = MagicMock(spec=LLMClient)
    client.complete.return_value = FindingsResponse(
        findings=[
            Finding(
                severity="high",
                category="security",
                title="SQL injection risk",
                description="User input is interpolated into query.",
                file_path="src/db.py",
                line_number=10,
                suggestion="Use parameterized queries.",
            ),
        ],
        summary="Found 1 security issue.",
    )
    return client


@pytest.fixture
def review_input() -> ReviewInput:
    """Minimal review input with one diff file."""
    return ReviewInput(
        diff_files=[
            DiffFile(
                filename="src/db.py",
                patch="@@ -1,3 +1,5 @@\n+import os\n",
                status="modified",
            ),
        ],
        pr_title="Fix database queries",
        pr_description="Parameterize all SQL queries.",
    )


@pytest.fixture
def empty_review_input() -> ReviewInput:
    """Review input with no diff files."""
    return ReviewInput(diff_files=[])


@pytest.fixture
def review_input_no_metadata() -> ReviewInput:
    """Review input with diff but no PR metadata."""
    return ReviewInput(
        diff_files=[
            DiffFile(
                filename="app.py",
                patch="@@ -1 +1 @@\n-old\n+new\n",
                status="modified",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# __init_subclass__ validation
# ---------------------------------------------------------------------------


class TestInitSubclassValidation:
    """Verify that subclass contract is enforced at class definition time."""

    def test_missing_name_raises(self) -> None:
        with pytest.raises(TypeError, match="must define class attribute 'name'"):

            class _BadAgent(BaseAgent):
                system_prompt = "prompt"

    def test_missing_system_prompt_raises(self) -> None:
        with pytest.raises(TypeError, match="must define class attribute 'system_prompt'"):

            class _BadAgent(BaseAgent):
                name = "missing_prompt_agent"

    def test_non_string_name_raises(self) -> None:
        with pytest.raises(TypeError, match="must be a str, got int"):

            class _BadAgent(BaseAgent):
                name = 42  # type: ignore[assignment]
                system_prompt = "prompt"

    def test_non_string_system_prompt_raises(self) -> None:
        with pytest.raises(TypeError, match="must be a str, got list"):

            class _BadAgent(BaseAgent):
                name = "bad_prompt_type_agent"
                system_prompt = ["not", "a", "string"]  # type: ignore[assignment]

    def test_empty_name_raises(self) -> None:
        with pytest.raises(TypeError, match="must not be empty or whitespace"):

            class _BadAgent(BaseAgent):
                name = ""
                system_prompt = "prompt"

    def test_whitespace_only_name_raises(self) -> None:
        with pytest.raises(TypeError, match="must not be empty or whitespace"):

            class _BadAgent(BaseAgent):
                name = "   "
                system_prompt = "prompt"

    def test_empty_system_prompt_raises(self) -> None:
        with pytest.raises(TypeError, match="must not be empty or whitespace"):

            class _BadAgent(BaseAgent):
                name = "empty_prompt_agent"
                system_prompt = "   "

    def test_invalid_name_format_uppercase_raises(self) -> None:
        with pytest.raises(TypeError, match="must be lowercase alphanumeric"):

            class _BadAgent(BaseAgent):
                name = "MyAgent"
                system_prompt = "prompt"

    def test_invalid_name_format_spaces_raises(self) -> None:
        with pytest.raises(TypeError, match="must be lowercase alphanumeric"):

            class _BadAgent(BaseAgent):
                name = "my agent"
                system_prompt = "prompt"

    def test_invalid_name_format_special_chars_raises(self) -> None:
        with pytest.raises(TypeError, match="must be lowercase alphanumeric"):

            class _BadAgent(BaseAgent):
                name = "my-agent!"
                system_prompt = "prompt"

    def test_invalid_name_starts_with_number_raises(self) -> None:
        with pytest.raises(TypeError, match="must be lowercase alphanumeric"):

            class _BadAgent(BaseAgent):
                name = "1agent"
                system_prompt = "prompt"

    def test_duplicate_name_raises(self) -> None:
        with pytest.raises(TypeError, match="already registered"):

            class _DuplicateAgent(BaseAgent):
                name = "security"  # already taken by SecurityAgent
                system_prompt = "prompt"

    def test_valid_subclass_accepted(self) -> None:
        class _ValidAgent(BaseAgent):
            name = "test_valid_subclass_agent"
            system_prompt = "A valid prompt."

        assert _ValidAgent.name == "test_valid_subclass_agent"

    def test_valid_name_with_underscores_and_numbers(self) -> None:
        class _ValidAgent(BaseAgent):
            name = "custom_agent_v2"
            system_prompt = "A valid prompt."

        assert _ValidAgent.name == "custom_agent_v2"


# ---------------------------------------------------------------------------
# review() -- success path
# ---------------------------------------------------------------------------


class TestReviewSuccess:
    """Verify the happy path through review()."""

    def test_returns_agent_result_with_findings(
        self, llm_client: MagicMock, review_input: ReviewInput
    ) -> None:
        agent = SecurityAgent(llm_client=llm_client)
        result = agent.review(review_input)

        assert result.agent_name == "security"
        assert result.status == AgentStatus.SUCCESS
        assert result.error_message is None
        assert len(result.findings) == 1
        assert result.findings[0].title == "SQL injection risk"
        assert result.summary == "Found 1 security issue."

    def test_execution_time_is_positive(
        self, llm_client: MagicMock, review_input: ReviewInput
    ) -> None:
        agent = SecurityAgent(llm_client=llm_client)
        result = agent.review(review_input)

        assert result.execution_time_seconds >= 0.0

    def test_llm_client_called_with_correct_args(
        self, llm_client: MagicMock, review_input: ReviewInput
    ) -> None:
        agent = SecurityAgent(llm_client=llm_client)
        agent.review(review_input)

        llm_client.complete.assert_called_once()
        call_kwargs = llm_client.complete.call_args.kwargs
        assert call_kwargs["response_model"] is FindingsResponse
        assert "security" in call_kwargs["system_prompt"].lower()
        assert "src/db.py" in call_kwargs["user_prompt"]

    def test_empty_findings_is_success(
        self, llm_client: MagicMock, review_input: ReviewInput
    ) -> None:
        llm_client.complete.return_value = FindingsResponse(
            findings=[],
            summary="No issues found.",
        )
        agent = SecurityAgent(llm_client=llm_client)
        result = agent.review(review_input)

        assert result.status == AgentStatus.SUCCESS
        assert len(result.findings) == 0
        assert result.summary == "No issues found."


# ---------------------------------------------------------------------------
# review() -- empty diff guard
# ---------------------------------------------------------------------------


class TestReviewEmptyDiff:
    """Verify behavior when there are no diff files to review."""

    def test_returns_success_with_no_findings(
        self, llm_client: MagicMock, empty_review_input: ReviewInput
    ) -> None:
        agent = SecurityAgent(llm_client=llm_client)
        result = agent.review(empty_review_input)

        assert result.status == AgentStatus.SUCCESS
        assert result.findings == []
        assert result.summary == "No code changes to review."

    def test_llm_not_called(self, llm_client: MagicMock, empty_review_input: ReviewInput) -> None:
        agent = SecurityAgent(llm_client=llm_client)
        agent.review(empty_review_input)

        llm_client.complete.assert_not_called()


# ---------------------------------------------------------------------------
# review() -- LLM error paths
# ---------------------------------------------------------------------------


class TestReviewLLMErrors:
    """Verify structured error handling for LLM failures."""

    def test_parse_error_returns_failed_result(
        self, llm_client: MagicMock, review_input: ReviewInput
    ) -> None:
        llm_client.complete.side_effect = LLMResponseParseError(
            raw_response="not json at all",
            model_name="FindingsResponse",
            cause=ValueError("bad json"),
        )
        agent = SecurityAgent(llm_client=llm_client)
        result = agent.review(review_input)

        assert result.status == AgentStatus.FAILED
        assert result.agent_name == "security"
        assert result.findings == []
        assert result.summary == ""
        assert result.error_message is not None
        assert "FindingsResponse" in result.error_message

    def test_empty_response_error_returns_failed_result(
        self, llm_client: MagicMock, review_input: ReviewInput
    ) -> None:
        llm_client.complete.side_effect = LLMEmptyResponseError("LLM returned empty response")
        agent = SecurityAgent(llm_client=llm_client)
        result = agent.review(review_input)

        assert result.status == AgentStatus.FAILED
        assert "empty response" in result.error_message.lower()

    def test_unexpected_error_returns_failed_result(
        self, llm_client: MagicMock, review_input: ReviewInput
    ) -> None:
        llm_client.complete.side_effect = RuntimeError("something unexpected")
        agent = SecurityAgent(llm_client=llm_client)
        result = agent.review(review_input)

        assert result.status == AgentStatus.FAILED
        assert "Unexpected error" in result.error_message
        assert "something unexpected" in result.error_message

    def test_failed_result_has_timing(
        self, llm_client: MagicMock, review_input: ReviewInput
    ) -> None:
        llm_client.complete.side_effect = LLMEmptyResponseError("empty")
        agent = SecurityAgent(llm_client=llm_client)
        result = agent.review(review_input)

        assert result.execution_time_seconds >= 0.0


# ---------------------------------------------------------------------------
# _format_user_prompt() -- prompt structure
# ---------------------------------------------------------------------------


class TestFormatUserPrompt:
    """Verify the user prompt is correctly assembled."""

    def test_includes_pr_metadata(self, llm_client: MagicMock, review_input: ReviewInput) -> None:
        agent = SecurityAgent(llm_client=llm_client)
        prompt = agent._format_user_prompt(review_input=review_input)

        assert "PR Title: Fix database queries" in prompt
        assert "PR Description: Parameterize all SQL queries." in prompt

    def test_includes_diff_with_markers(
        self, llm_client: MagicMock, review_input: ReviewInput
    ) -> None:
        agent = SecurityAgent(llm_client=llm_client)
        prompt = agent._format_user_prompt(review_input=review_input)

        assert "START ---" in prompt
        assert "END ---" in prompt
        assert "File: src/db.py (status: modified)" in prompt
        assert "+import os" in prompt

    def test_no_metadata_when_absent(
        self, llm_client: MagicMock, review_input_no_metadata: ReviewInput
    ) -> None:
        agent = SecurityAgent(llm_client=llm_client)
        prompt = agent._format_user_prompt(review_input=review_input_no_metadata)

        assert "PR Title" not in prompt
        assert "PR Description" not in prompt
        assert "START ---" in prompt

    def test_previous_findings_included(
        self, llm_client: MagicMock, review_input: ReviewInput
    ) -> None:
        previous = [
            Finding(
                severity="high",
                category="security",
                title="SQL injection found",
                description="User input in query.",
            ),
        ]
        agent = SecurityAgent(llm_client=llm_client)
        prompt = agent._format_user_prompt(
            review_input=review_input,
            previous_findings=previous,
        )

        assert "PREVIOUS FINDINGS" in prompt
        assert "--- PREVIOUS FINDINGS END ---" in prompt
        assert "SQL injection found" in prompt
        assert "Do NOT repeat the findings above" in prompt

    def test_no_previous_findings_section_when_empty(
        self, llm_client: MagicMock, review_input: ReviewInput
    ) -> None:
        agent = SecurityAgent(llm_client=llm_client)
        prompt = agent._format_user_prompt(review_input=review_input)

        assert "PREVIOUS FINDINGS" not in prompt

    def test_no_previous_findings_section_when_none(
        self, llm_client: MagicMock, review_input: ReviewInput
    ) -> None:
        agent = SecurityAgent(llm_client=llm_client)
        prompt = agent._format_user_prompt(
            review_input=review_input,
            previous_findings=None,
        )

        assert "PREVIOUS FINDINGS" not in prompt

    def test_multiple_diff_files(self, llm_client: MagicMock) -> None:
        review_input = ReviewInput(
            diff_files=[
                DiffFile(filename="a.py", patch="+line a", status="added"),
                DiffFile(filename="b.py", patch="+line b", status="modified"),
                DiffFile(filename="c.py", patch="-line c", status="deleted"),
            ],
        )
        agent = SecurityAgent(llm_client=llm_client)
        prompt = agent._format_user_prompt(review_input=review_input)

        assert "File: a.py (status: added)" in prompt
        assert "File: b.py (status: modified)" in prompt
        assert "File: c.py (status: deleted)" in prompt


# ---------------------------------------------------------------------------
# _extra_context() -- override hook
# ---------------------------------------------------------------------------


class TestExtraContext:
    """Verify the _extra_context hook works correctly."""

    def test_default_returns_none(self, llm_client: MagicMock, review_input: ReviewInput) -> None:
        agent = SecurityAgent(llm_client=llm_client)
        assert agent._extra_context(review_input) is None

    def test_extra_context_included_in_prompt(
        self, llm_client: MagicMock, review_input: ReviewInput
    ) -> None:
        class _ContextAgent(BaseAgent):
            name = "context_test_agent"
            system_prompt = "Test prompt."

            def _extra_context(self, review_input: ReviewInput) -> str:
                return "Extra context: check auth files carefully."

        agent = _ContextAgent(llm_client=llm_client)
        prompt = agent._format_user_prompt(review_input=review_input)

        assert "Extra context: check auth files carefully." in prompt
        # Extra context should appear between metadata and diff
        meta_pos = prompt.find("PR Title")
        extra_pos = prompt.find("Extra context")
        diff_pos = prompt.find("START ---")
        assert meta_pos < extra_pos < diff_pos

    def test_whitespace_only_extra_context_excluded(
        self, llm_client: MagicMock, review_input: ReviewInput
    ) -> None:
        class _WhitespaceAgent(BaseAgent):
            name = "whitespace_ctx_agent"
            system_prompt = "Test prompt."

            def _extra_context(self, review_input: ReviewInput) -> str:
                return "   "

        agent = _WhitespaceAgent(llm_client=llm_client)
        prompt = agent._format_user_prompt(review_input=review_input)

        # Whitespace-only extra context should not appear
        lines = [line.strip() for line in prompt.split("\n") if line.strip()]
        assert "   " not in lines

    def test_wrong_return_type_raises(
        self, llm_client: MagicMock, review_input: ReviewInput
    ) -> None:
        class _BadReturnAgent(BaseAgent):
            name = "bad_return_ctx_agent"
            system_prompt = "Test prompt."

            def _extra_context(self, review_input: ReviewInput) -> str | None:
                return 42  # type: ignore[return-value]

        agent = _BadReturnAgent(llm_client=llm_client)

        with pytest.raises(TypeError, match="_extra_context must return str or None"):
            agent._format_user_prompt(review_input=review_input)

    def test_wrong_return_type_caught_by_review(
        self, llm_client: MagicMock, review_input: ReviewInput
    ) -> None:
        """TypeError from _extra_context is caught by review() catch-all."""

        class _BadReturnAgent2(BaseAgent):
            name = "bad_return_ctx_agent2"
            system_prompt = "Test prompt."

            def _extra_context(self, review_input: ReviewInput) -> str | None:
                return ["wrong"]  # type: ignore[return-value]

        agent = _BadReturnAgent2(llm_client=llm_client)
        result = agent.review(review_input)

        assert result.status == AgentStatus.FAILED
        assert "Unexpected error" in result.error_message

    def test_exception_in_extra_context_caught_by_review(
        self, llm_client: MagicMock, review_input: ReviewInput
    ) -> None:
        """Buggy _extra_context override is caught by review() catch-all."""

        class _CrashingAgent(BaseAgent):
            name = "crashing_ctx_agent"
            system_prompt = "Test prompt."

            def _extra_context(self, review_input: ReviewInput) -> str | None:
                msg = "bug in custom agent"
                raise RuntimeError(msg)

        agent = _CrashingAgent(llm_client=llm_client)
        result = agent.review(review_input)

        assert result.status == AgentStatus.FAILED
        assert "bug in custom agent" in result.error_message


# ---------------------------------------------------------------------------
# Existing agent subclasses -- smoke test
# ---------------------------------------------------------------------------


class TestExistingAgents:
    """Verify all shipped agents pass validation and can review."""

    @pytest.mark.parametrize(
        "agent_name",
        ["security", "performance", "style", "test_coverage"],
    )
    def test_agent_exists_and_has_valid_name(self, agent_name: str) -> None:
        assert agent_name in BaseAgent._registered_names

    def test_all_agents_review_successfully(
        self, llm_client: MagicMock, review_input: ReviewInput
    ) -> None:
        from code_review_agent.agents import (
            PerformanceAgent,
            SecurityAgent,
            StyleAgent,
            TestCoverageAgent,
        )

        for agent_cls in (SecurityAgent, PerformanceAgent, StyleAgent, TestCoverageAgent):
            agent = agent_cls(llm_client=llm_client)
            result = agent.review(review_input)
            assert result.status == AgentStatus.SUCCESS
            assert result.agent_name == agent_cls.name
