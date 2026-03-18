from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from code_review_agent.config import Settings
from code_review_agent.llm_client import LLMClient
from code_review_agent.models import (
    AgentResult,
    DiffFile,
    Finding,
    FindingsResponse,
    ReviewInput,
    ReviewReport,
    SynthesisResponse,
)


@pytest.fixture
def sample_diff_files() -> list[DiffFile]:
    """Realistic Python code diffs for testing."""
    return [
        DiffFile(
            filename="src/auth/login.py",
            patch=(
                "@@ -10,6 +10,12 @@\n"
                " def authenticate(username: str, password: str) -> bool:\n"
                "-    query = f\"SELECT * FROM users WHERE name='{username}'\"\n"
                '+    query = "SELECT * FROM users WHERE name = %s"\n'
                "+    cursor.execute(query, (username,))\n"
                "     user = cursor.fetchone()\n"
                "+    if user is None:\n"
                "+        return False\n"
                "+    return verify_password(password, user.hashed_password)\n"
            ),
            status="modified",
        ),
        DiffFile(
            filename="src/utils/cache.py",
            patch=(
                "@@ -1,4 +1,15 @@\n"
                "+from functools import lru_cache\n"
                "+\n"
                "+\n"
                "+@lru_cache(maxsize=256)\n"
                " def get_config(key: str) -> str:\n"
                "     return _load_config()[key]\n"
                "+\n"
                "+\n"
                "+def invalidate_cache() -> None:\n"
                "+    get_config.cache_clear()\n"
            ),
            status="modified",
        ),
    ]


@pytest.fixture
def sample_review_input(sample_diff_files: list[DiffFile]) -> ReviewInput:
    """ReviewInput assembled from sample diffs."""
    return ReviewInput(
        pr_url="https://github.com/acme/webapp/pull/42",
        pr_title="Fix SQL injection and add caching",
        diff_files=sample_diff_files,
    )


@pytest.fixture
def sample_finding() -> Finding:
    """A single representative Finding."""
    return Finding(
        file_path="src/auth/login.py",
        line_number=12,
        severity="high",
        category="security",
        title="SQL injection vulnerability fixed",
        description=(
            "The original code used f-string interpolation in a SQL query, "
            "which is vulnerable to SQL injection. The fix correctly uses "
            "parameterized queries."
        ),
        suggestion="No further action needed -- the fix is correct.",
    )


@pytest.fixture
def sample_agent_result(sample_finding: Finding) -> AgentResult:
    """An AgentResult from the security agent."""
    return AgentResult(
        agent_name="security",
        findings=[
            sample_finding,
            Finding(
                file_path="src/auth/login.py",
                line_number=15,
                severity="low",
                category="security",
                title="Consider constant-time comparison for password verification",
                description=(
                    "Ensure verify_password uses a constant-time comparison "
                    "to prevent timing attacks."
                ),
                suggestion="Use hmac.compare_digest or a dedicated library.",
            ),
        ],
        summary="Found 2 security-related issues in the authentication code.",
        execution_time_seconds=1.5,
    )


@pytest.fixture
def sample_review_report(sample_agent_result: AgentResult) -> ReviewReport:
    """A ReviewReport with results from multiple agents."""
    performance_result = AgentResult(
        agent_name="performance",
        findings=[
            Finding(
                file_path="src/utils/cache.py",
                line_number=4,
                severity="medium",
                category="performance",
                title="Unbounded LRU cache may consume excessive memory",
                description=(
                    "The lru_cache maxsize of 256 may be too large if config "
                    "values are large strings. Monitor memory usage."
                ),
                suggestion="Consider a smaller maxsize or use a TTL cache.",
            ),
        ],
        summary="Found 1 performance concern with caching.",
        execution_time_seconds=1.2,
    )

    style_result = AgentResult(
        agent_name="style",
        findings=[],
        summary="No style issues found.",
        execution_time_seconds=0.8,
    )

    test_coverage_result = AgentResult(
        agent_name="test_coverage",
        findings=[
            Finding(
                file_path="src/auth/login.py",
                line_number=10,
                severity="high",
                category="test_coverage",
                title="No tests for authenticate function",
                description=(
                    "The authenticate function has no corresponding unit tests. "
                    "Critical authentication logic must be tested."
                ),
                suggestion=(
                    "Add tests covering: valid login, invalid password, "
                    "nonexistent user, and SQL injection attempts."
                ),
            ),
        ],
        summary="Missing test coverage for critical auth code.",
        execution_time_seconds=1.1,
    )

    return ReviewReport(
        pr_url="https://github.com/acme/webapp/pull/42",
        reviewed_at=datetime(2026, 3, 12, 10, 0, 0, tzinfo=UTC),
        agent_results=[
            sample_agent_result,
            performance_result,
            style_result,
            test_coverage_result,
        ],
        overall_summary="The PR fixes a SQL injection but lacks test coverage.",
        risk_level="high",
    )


@pytest.fixture
def mock_settings() -> Settings:
    """Settings with fake API key and test values.

    Explicitly sets all optional fields to prevent .env file values
    from leaking into tests.
    """
    return Settings(
        nvidia_api_key="nvapi-test-fake-key-00000000",  # pragma: allowlist secret
        openrouter_api_key="sk-or-test-fake-key-00000000",  # pragma: allowlist secret
        llm_provider="nvidia",
        llm_model="nvidia/nemotron-3-super-120b-a12b",
        github_token="ghp_test_fake_token_00000000",  # pragma: allowlist secret
        max_tokens_per_review=None,
        max_prompt_tokens=None,
        rate_limit_rpm=None,
    )


@pytest.fixture
def mock_llm_client() -> MagicMock:
    """A MagicMock standing in for LLMClient."""
    client = MagicMock(spec=LLMClient)
    client.complete.return_value = FindingsResponse(
        findings=[
            Finding(
                file_path="src/app.py",
                line_number=1,
                severity="medium",
                category="test",
                title="Mock finding",
                description="This is a mocked LLM response.",
                suggestion="No action needed.",
            ),
        ],
        summary="Mock summary.",
    )
    return client


@pytest.fixture
def mock_synthesis_response() -> SynthesisResponse:
    """A mock synthesis response for orchestrator tests."""
    return SynthesisResponse(
        overall_summary="Overall the code looks reasonable with minor issues.",
        risk_level="medium",
    )
