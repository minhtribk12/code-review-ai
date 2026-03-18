"""Lightweight LLM connection test using minimal tokens."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

import openai
import structlog

if TYPE_CHECKING:
    from code_review_agent.config import Settings

logger = structlog.get_logger(__name__)

_TEST_TIMEOUT_SECONDS = 15.0
_TEST_PROMPT = "hi"


class FailureKind(StrEnum):
    """Category of connection test failure."""

    NONE = "none"
    MODEL = "model"
    PROVIDER = "provider"
    UNKNOWN = "unknown"


def test_llm_connection(settings: Settings) -> tuple[bool, str, FailureKind]:
    """Send a minimal request to the LLM to verify connectivity.

    Uses max_tokens=1 and a trivial prompt to consume as few tokens
    as possible. Returns (success, message, failure_kind).
    """
    try:
        client = openai.OpenAI(
            api_key=settings.resolved_api_key.get_secret_value(),
            base_url=settings.resolved_llm_base_url,
            timeout=_TEST_TIMEOUT_SECONDS,
        )
        response = client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": _TEST_PROMPT}],
            max_tokens=1,
        )
        if response.choices:
            model_used = getattr(response, "model", settings.llm_model)
            return True, f"Connected to {model_used}", FailureKind.NONE

        return False, "Server returned empty response", FailureKind.UNKNOWN

    except openai.NotFoundError as exc:
        return False, f"Model not found: {exc.message}", FailureKind.MODEL
    except openai.AuthenticationError as exc:
        return False, f"Authentication failed: {exc.message}", FailureKind.PROVIDER
    except openai.APIConnectionError as exc:
        return False, f"Connection failed: {exc}", FailureKind.PROVIDER
    except openai.APITimeoutError:
        return False, f"Connection timed out ({_TEST_TIMEOUT_SECONDS:.0f}s)", FailureKind.PROVIDER
    except openai.RateLimitError:
        return True, "Connected (rate limited, but reachable)", FailureKind.NONE
    except openai.APIStatusError as exc:
        # 4xx errors other than 401/404 are likely model issues
        if 400 <= exc.status_code < 500:
            return False, f"API error {exc.status_code}: {exc.message}", FailureKind.MODEL
        return False, f"API error {exc.status_code}: {exc.message}", FailureKind.PROVIDER
    except Exception as exc:
        return False, f"Unexpected error: {exc}", FailureKind.UNKNOWN
