from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from code_review_agent.llm_client import (
    LLMEmptyResponseError,
    LLMResponseParseError,
    _extract_json_object,
    _strip_markdown_fences,
)

# ---------------------------------------------------------------------------
# Helper model for tests
# ---------------------------------------------------------------------------


class _SimpleModel(BaseModel):
    name: str
    value: int


# ---------------------------------------------------------------------------
# _strip_markdown_fences
# ---------------------------------------------------------------------------


class TestStripMarkdownFences:
    """Test markdown fence removal from LLM responses."""

    def test_no_fences(self) -> None:
        assert _strip_markdown_fences('{"a": 1}') == '{"a": 1}'

    def test_json_fences(self) -> None:
        raw = '```json\n{"a": 1}\n```'
        assert _strip_markdown_fences(raw) == '{"a": 1}'

    def test_plain_fences(self) -> None:
        raw = '```\n{"a": 1}\n```'
        assert _strip_markdown_fences(raw) == '{"a": 1}'

    def test_no_newline_after_fence(self) -> None:
        raw = '```{"a": 1}```'
        assert _strip_markdown_fences(raw) == '{"a": 1}'

    def test_extra_whitespace(self) -> None:
        raw = '  ```json\n  {"a": 1}\n  ```  '
        assert _strip_markdown_fences(raw) == '{"a": 1}'

    def test_nested_backticks_in_content(self) -> None:
        """Backticks inside JSON content should not be stripped."""
        raw = '```json\n{"code": "use `var`"}\n```'
        assert _strip_markdown_fences(raw) == '{"code": "use `var`"}'

    def test_only_opening_fence(self) -> None:
        raw = '```json\n{"a": 1}'
        result = _strip_markdown_fences(raw)
        assert '"a": 1' in result

    def test_only_closing_fence(self) -> None:
        raw = '{"a": 1}\n```'
        result = _strip_markdown_fences(raw)
        assert '"a": 1' in result


# ---------------------------------------------------------------------------
# _extract_json_object
# ---------------------------------------------------------------------------


class TestExtractJsonObject:
    """Test JSON extraction from text with surrounding content."""

    def test_clean_json(self) -> None:
        assert _extract_json_object('{"a": 1}') == '{"a": 1}'

    def test_json_with_preamble(self) -> None:
        text = 'Here is the result:\n{"name": "test", "value": 42}'
        result = _extract_json_object(text)
        assert result is not None
        assert '"name": "test"' in result

    def test_json_with_trailing_text(self) -> None:
        text = '{"name": "test", "value": 42}\nHope this helps!'
        result = _extract_json_object(text)
        assert result is not None
        assert '"value": 42' in result

    def test_json_with_preamble_and_trailing(self) -> None:
        text = 'Analysis:\n{"name": "x", "value": 1}\nDone.'
        result = _extract_json_object(text)
        assert result is not None

    def test_no_json_returns_none(self) -> None:
        assert _extract_json_object("just plain text") is None

    def test_invalid_json_returns_none(self) -> None:
        assert _extract_json_object("{invalid json}") is None

    def test_empty_string_returns_none(self) -> None:
        assert _extract_json_object("") is None

    def test_nested_json(self) -> None:
        text = '{"outer": {"inner": 1}, "value": 2}'
        result = _extract_json_object(text)
        assert result is not None
        assert "inner" in result


# ---------------------------------------------------------------------------
# LLMEmptyResponseError
# ---------------------------------------------------------------------------


class TestLLMEmptyResponseError:
    """Test custom exception behavior."""

    def test_message(self) -> None:
        err = LLMEmptyResponseError("LLM returned empty response")
        assert "empty response" in str(err)


# ---------------------------------------------------------------------------
# LLMResponseParseError
# ---------------------------------------------------------------------------


class TestLLMResponseParseError:
    """Test parse error with truncation."""

    def test_short_response_not_truncated(self) -> None:
        err = LLMResponseParseError(
            raw_response="short text",
            model_name="TestModel",
            cause=ValueError("bad"),
        )
        assert "short text" in str(err)
        assert "truncated" not in str(err)
        assert "TestModel" in str(err)

    def test_long_response_truncated(self) -> None:
        long_text = "x" * 1000
        err = LLMResponseParseError(
            raw_response=long_text,
            model_name="TestModel",
            cause=ValueError("bad"),
        )
        assert "truncated" in str(err)
        assert len(str(err)) < 1000

    def test_raw_response_preserved(self) -> None:
        err = LLMResponseParseError(
            raw_response="full raw text here",
            model_name="TestModel",
            cause=ValueError("bad"),
        )
        assert err.raw_response == "full raw text here"


# ---------------------------------------------------------------------------
# LLMClient.complete() -- three-layer parsing
# ---------------------------------------------------------------------------


class TestLLMClientComplete:
    """Test the complete() method's three-layer parsing strategy."""

    def _make_client(self) -> MagicMock:
        """Create a mock LLMClient with a mock OpenAI client."""
        import threading

        from code_review_agent.llm_client import LLMClient
        from code_review_agent.rate_limiter import NoOpRateLimiter

        with patch.object(LLMClient, "__init__", lambda self, settings: None):
            client = LLMClient.__new__(LLMClient)
            client._model = "test-model"
            client._temperature = 0.1
            client._client = MagicMock()
            client._rate_limiter = NoOpRateLimiter()
            client._usage_lock = threading.Lock()
            client._total_prompt_tokens = 0
            client._total_completion_tokens = 0
            client._llm_calls = 0
        return client

    def _mock_response(self, content: str) -> MagicMock:
        """Build a mock OpenAI ChatCompletion response."""
        message = MagicMock()
        message.content = content

        choice = MagicMock()
        choice.message = message

        usage = MagicMock()
        usage.prompt_tokens = 100
        usage.completion_tokens = 50
        usage.total_tokens = 150

        response = MagicMock()
        response.choices = [choice]
        response.usage = usage
        return response

    def test_layer1_clean_json(self) -> None:
        client = self._make_client()
        client._client.chat.completions.create.return_value = self._mock_response(
            '{"name": "test", "value": 42}'
        )
        result = client.complete(
            system_prompt="test",
            user_prompt="test",
            response_model=_SimpleModel,
        )
        assert result.name == "test"
        assert result.value == 42

    def test_layer1_json_with_fences(self) -> None:
        client = self._make_client()
        client._client.chat.completions.create.return_value = self._mock_response(
            '```json\n{"name": "fenced", "value": 1}\n```'
        )
        result = client.complete(
            system_prompt="test",
            user_prompt="test",
            response_model=_SimpleModel,
        )
        assert result.name == "fenced"

    def test_layer2_json_with_preamble(self) -> None:
        client = self._make_client()
        client._client.chat.completions.create.return_value = self._mock_response(
            'Here is the analysis:\n{"name": "extracted", "value": 7}'
        )
        result = client.complete(
            system_prompt="test",
            user_prompt="test",
            response_model=_SimpleModel,
        )
        assert result.name == "extracted"

    def test_layer3_retry_on_bad_then_good(self) -> None:
        client = self._make_client()
        client._client.chat.completions.create.side_effect = [
            self._mock_response("totally not json"),
            self._mock_response('{"name": "retry_worked", "value": 99}'),
        ]
        result = client.complete(
            system_prompt="test",
            user_prompt="test",
            response_model=_SimpleModel,
        )
        assert result.name == "retry_worked"
        assert client._client.chat.completions.create.call_count == 2

    def test_all_layers_fail_raises_parse_error(self) -> None:
        client = self._make_client()
        client._client.chat.completions.create.return_value = self._mock_response(
            "completely unparseable garbage"
        )
        with pytest.raises(LLMResponseParseError, match="_SimpleModel"):
            client.complete(
                system_prompt="test",
                user_prompt="test",
                response_model=_SimpleModel,
            )

    def test_empty_choices_raises(self) -> None:
        client = self._make_client()
        response = MagicMock()
        response.choices = []
        client._client.chat.completions.create.return_value = response

        with pytest.raises(LLMEmptyResponseError, match="no choices"):
            client.complete(
                system_prompt="test",
                user_prompt="test",
                response_model=_SimpleModel,
            )

    def test_empty_content_raises(self) -> None:
        client = self._make_client()
        client._client.chat.completions.create.return_value = self._mock_response("")

        with pytest.raises(LLMEmptyResponseError, match="empty response"):
            client.complete(
                system_prompt="test",
                user_prompt="test",
                response_model=_SimpleModel,
            )

    def test_whitespace_content_raises(self) -> None:
        client = self._make_client()
        client._client.chat.completions.create.return_value = self._mock_response("   \n  ")

        with pytest.raises(LLMEmptyResponseError, match="empty response"):
            client.complete(
                system_prompt="test",
                user_prompt="test",
                response_model=_SimpleModel,
            )

    def test_none_content_raises(self) -> None:
        client = self._make_client()
        message = MagicMock()
        message.content = None
        choice = MagicMock()
        choice.message = message
        response = MagicMock()
        response.choices = [choice]
        client._client.chat.completions.create.return_value = response

        with pytest.raises(LLMEmptyResponseError, match="empty response"):
            client.complete(
                system_prompt="test",
                user_prompt="test",
                response_model=_SimpleModel,
            )

    def test_schema_injected_into_system_prompt(self) -> None:
        client = self._make_client()
        client._client.chat.completions.create.return_value = self._mock_response(
            '{"name": "x", "value": 1}'
        )
        client.complete(
            system_prompt="You are a test agent.",
            user_prompt="test input",
            response_model=_SimpleModel,
        )

        call_args = client._client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        system_content = messages[0]["content"]
        assert "You are a test agent." in system_content
        assert "JSON" in system_content
        assert "name" in system_content
        assert "value" in system_content

    def test_no_usage_does_not_crash(self) -> None:
        client = self._make_client()
        response = self._mock_response('{"name": "x", "value": 1}')
        response.usage = None
        client._client.chat.completions.create.return_value = response

        result = client.complete(
            system_prompt="test",
            user_prompt="test",
            response_model=_SimpleModel,
        )
        assert result.name == "x"

    def test_get_usage_tracks_cumulative_tokens(self) -> None:
        client = self._make_client()
        client._client.chat.completions.create.return_value = self._mock_response(
            '{"name": "a", "value": 1}'
        )
        client.complete(system_prompt="t", user_prompt="t", response_model=_SimpleModel)

        usage = client.get_usage()
        assert usage.prompt_tokens == 100
        assert usage.completion_tokens == 50
        assert usage.total_tokens == 150
        assert usage.llm_calls == 1

    def test_get_usage_accumulates_across_calls(self) -> None:
        client = self._make_client()
        client._client.chat.completions.create.return_value = self._mock_response(
            '{"name": "a", "value": 1}'
        )
        client.complete(system_prompt="t", user_prompt="t", response_model=_SimpleModel)
        client.complete(system_prompt="t", user_prompt="t", response_model=_SimpleModel)

        usage = client.get_usage()
        assert usage.prompt_tokens == 200
        assert usage.completion_tokens == 100
        assert usage.total_tokens == 300
        assert usage.llm_calls == 2

    def test_get_usage_initial_zero(self) -> None:
        client = self._make_client()
        usage = client.get_usage()
        assert usage.total_tokens == 0
        assert usage.llm_calls == 0

    def test_get_usage_no_usage_header_still_counts_call(self) -> None:
        """LLM call is counted even when provider omits usage data."""
        client = self._make_client()
        response = self._mock_response('{"name": "a", "value": 1}')
        response.usage = None
        client._client.chat.completions.create.return_value = response

        client.complete(system_prompt="t", user_prompt="t", response_model=_SimpleModel)

        usage = client.get_usage()
        assert usage.total_tokens == 0
        assert usage.llm_calls == 1
