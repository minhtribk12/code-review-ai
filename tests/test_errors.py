"""Tests for the structured error display and exception classification system."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from rich.console import Console

from code_review_agent.error_guidance import (
    _classify_http_status,
    _classify_value_error,
    _extract_status_code,
    classify_exception,
)
from code_review_agent.errors import UserError, print_error, print_error_cli

# ---------------------------------------------------------------------------
# UserError
# ---------------------------------------------------------------------------


class TestUserError:
    def test_detail_only(self) -> None:
        err = UserError(detail="Something broke")
        assert err.detail == "Something broke"
        assert err.reason is None
        assert err.solution is None

    def test_full_error(self) -> None:
        err = UserError(
            detail="Cannot connect",
            reason="Network is down",
            solution="Check your wifi",
        )
        assert err.detail == "Cannot connect"
        assert err.reason == "Network is down"
        assert err.solution == "Check your wifi"

    def test_frozen(self) -> None:
        err = UserError(detail="test")
        with pytest.raises(AttributeError):
            err.detail = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# print_error (TUI)
# ---------------------------------------------------------------------------


class TestPrintError:
    def test_renders_panel_with_detail(self) -> None:
        buf = StringIO()
        con = Console(file=buf, width=120, no_color=True)
        err = UserError(detail="File not found")
        print_error(err, console=con)
        output = buf.getvalue()
        assert "File not found" in output
        assert "Error" in output  # Panel title

    def test_renders_reason_when_present(self) -> None:
        buf = StringIO()
        con = Console(file=buf, width=120, no_color=True)
        err = UserError(detail="Auth failed", reason="Token expired")
        print_error(err, console=con)
        output = buf.getvalue()
        assert "Token expired" in output
        assert "Reason:" in output

    def test_renders_solution_when_present(self) -> None:
        buf = StringIO()
        con = Console(file=buf, width=120, no_color=True)
        err = UserError(detail="Auth failed", solution="Renew your token")
        print_error(err, console=con)
        output = buf.getvalue()
        assert "Renew your token" in output
        assert "Fix:" in output

    def test_full_error_has_all_sections(self) -> None:
        buf = StringIO()
        con = Console(file=buf, width=120, no_color=True)
        err = UserError(
            detail="Connection refused",
            reason="Server is down",
            solution="Retry later",
        )
        print_error(err, console=con)
        output = buf.getvalue()
        assert "Connection refused" in output
        assert "Reason:" in output
        assert "Server is down" in output
        assert "Fix:" in output
        assert "Retry later" in output

    def test_creates_console_when_none(self) -> None:
        buf = StringIO()
        err = UserError(detail="auto console test")
        fake_con = Console(file=buf, width=120, no_color=True)
        with patch("rich.console.Console", return_value=fake_con):
            print_error(err)
        assert "auto console test" in buf.getvalue()


# ---------------------------------------------------------------------------
# print_error_cli
# ---------------------------------------------------------------------------


class TestPrintErrorCli:
    def test_detail_only(self, capsys: pytest.CaptureFixture[str]) -> None:
        err = UserError(detail="Bad input")
        print_error_cli(err)
        output = capsys.readouterr().err
        assert "Error: Bad input" in output

    def test_full_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        err = UserError(
            detail="Missing key",
            reason="No .env file",
            solution="Run: cp .env.example .env",
        )
        print_error_cli(err)
        output = capsys.readouterr().err
        assert "Error: Missing key" in output
        assert "Reason: No .env file" in output
        assert "Fix: Run: cp .env.example .env" in output


# ---------------------------------------------------------------------------
# classify_exception
# ---------------------------------------------------------------------------


class TestClassifyException:
    def test_github_auth_error(self) -> None:
        exc = type("GitHubAuthError", (Exception,), {})("denied")
        result = classify_exception(exc)
        assert "authentication" in result.detail.lower()
        assert result.reason is not None
        assert "GITHUB_TOKEN" in (result.solution or "")

    def test_github_rate_limit_exhausted(self) -> None:
        exc = type("GitHubRateLimitExhausted", (Exception,), {})("limit hit")
        result = classify_exception(exc)
        assert "rate limit" in result.detail.lower()
        assert result.solution is not None

    def test_openai_auth_error(self) -> None:
        exc = type("AuthenticationError", (Exception,), {})("invalid key")
        result = classify_exception(exc)
        assert "authentication" in result.detail.lower()
        assert "api key" in (result.reason or "").lower()

    def test_openai_not_found(self) -> None:
        exc = type("NotFoundError", (Exception,), {})("model xyz not found")
        result = classify_exception(exc)
        assert "model not found" in result.detail.lower()
        assert "provider models" in (result.solution or "").lower()

    def test_openai_connection_error(self) -> None:
        exc = type("APIConnectionError", (Exception,), {})("refused")
        result = classify_exception(exc)
        assert "cannot reach" in result.detail.lower()

    def test_openai_timeout(self) -> None:
        exc = type("APITimeoutError", (Exception,), {})("timed out")
        result = classify_exception(exc)
        assert "timed out" in result.detail.lower()

    def test_openai_rate_limit(self) -> None:
        exc = type("RateLimitError", (Exception,), {})("too fast")
        result = classify_exception(exc)
        assert "rate limit" in result.detail.lower()

    def test_llm_parse_error(self) -> None:
        exc = type("LLMResponseParseError", (Exception,), {})("bad json")
        result = classify_exception(exc)
        assert "parse" in result.detail.lower()
        assert result.solution is not None

    def test_llm_empty_response(self) -> None:
        exc = type("LLMEmptyResponseError", (Exception,), {})("empty")
        result = classify_exception(exc)
        assert "empty response" in result.detail.lower()

    def test_validation_error(self) -> None:
        exc = type("ValidationError", (Exception,), {})("field X invalid")
        result = classify_exception(exc)
        assert "validation" in result.detail.lower()
        assert "config validate" in (result.solution or "").lower()

    def test_file_not_found(self) -> None:
        result = classify_exception(FileNotFoundError("no/such/file"))
        assert "file not found" in result.detail.lower()
        assert result.solution is not None

    def test_permission_error(self) -> None:
        result = classify_exception(PermissionError("/etc/secret"))
        assert "permission denied" in result.detail.lower()

    def test_git_error(self) -> None:
        exc = type("GitError", (Exception,), {})("not a repo")
        result = classify_exception(exc)
        assert "not a repo" in result.detail
        assert result.reason is not None

    def test_value_error_api_key(self) -> None:
        result = classify_exception(ValueError("Missing api_key for provider"))
        assert "api key" in result.detail.lower()
        assert ".env" in (result.solution or "")

    def test_value_error_pr_reference(self) -> None:
        result = classify_exception(ValueError("Invalid PR reference: xyz"))
        assert "pr reference" in result.detail.lower()
        assert "owner/repo#123" in (result.solution or "")

    def test_value_error_generic(self) -> None:
        result = classify_exception(ValueError("some random error"))
        assert result.detail == "some random error"

    def test_unknown_exception(self) -> None:
        result = classify_exception(RuntimeError("weird thing happened"))
        assert "weird thing happened" in result.detail

    def test_context_prepended(self) -> None:
        result = classify_exception(RuntimeError("boom"), context="review")
        assert result.detail.startswith("review: ")

    def test_context_prepended_to_classified(self) -> None:
        exc = type("GitHubAuthError", (Exception,), {})("denied")
        result = classify_exception(exc, context="PR fetch")
        assert result.detail.startswith("PR fetch: ")


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestClassifyHttpStatus:
    def test_401(self) -> None:
        result = _classify_http_status(401, "", "Unauthorized")
        assert "authentication" in result.detail.lower()
        assert result.solution is not None

    def test_404(self) -> None:
        result = _classify_http_status(404, "", "Not found")
        assert "not found" in result.detail.lower()

    def test_422(self) -> None:
        result = _classify_http_status(422, "", "Validation failed")
        assert "validation" in result.detail.lower()

    def test_429(self) -> None:
        result = _classify_http_status(429, "", "Rate limited")
        assert "rate limit" in result.detail.lower()

    def test_500(self) -> None:
        result = _classify_http_status(500, "", "Internal server error")
        assert "server error" in result.detail.lower()

    def test_unknown_status(self) -> None:
        result = _classify_http_status(418, "", "I'm a teapot")
        assert "http error" in result.detail.lower()

    def test_none_status(self) -> None:
        result = _classify_http_status(None, "", "unknown")
        assert "http error" in result.detail.lower()


class TestClassifyValueError:
    def test_api_key_pattern(self) -> None:
        result = _classify_value_error("", "Missing api_key for nvidia")
        assert "api key" in result.detail.lower()

    def test_pr_reference_pattern(self) -> None:
        result = _classify_value_error("", "Invalid PR reference: foo")
        assert "pr reference" in result.detail.lower()

    def test_no_git_remote_pattern(self) -> None:
        result = _classify_value_error("", "No git remote found")
        assert "git remote" in result.detail.lower()

    def test_generic_value_error(self) -> None:
        result = _classify_value_error("ctx: ", "something else")
        assert result.detail == "ctx: something else"


class TestExtractStatusCode:
    def test_extracts_from_message(self) -> None:
        assert _extract_status_code("HTTP 404 Not Found") == 404

    def test_extracts_401(self) -> None:
        assert _extract_status_code("GitHub API returned 401") == 401

    def test_no_code(self) -> None:
        assert _extract_status_code("something went wrong") is None

    def test_ignores_non_http_numbers(self) -> None:
        assert _extract_status_code("processed 50 items") is None
