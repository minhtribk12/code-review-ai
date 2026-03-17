from __future__ import annotations

import json
import re
import threading
from typing import TYPE_CHECKING, TypeVar

import openai
import structlog
from pydantic import BaseModel, ValidationError
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from code_review_agent.models import TokenUsage
from code_review_agent.rate_limiter import RateLimiter, create_rate_limiter

if TYPE_CHECKING:
    from code_review_agent.config import Settings

logger = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

_MAX_RAW_LOG_LENGTH = 500
_JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)


class LLMEmptyResponseError(Exception):
    """Raised when the LLM returns an empty or missing response."""


class LLMResponseParseError(Exception):
    """Raised when the LLM response cannot be parsed into the expected model."""

    def __init__(self, raw_response: str, model_name: str, cause: Exception) -> None:
        truncated = raw_response[:_MAX_RAW_LOG_LENGTH]
        if len(raw_response) > _MAX_RAW_LOG_LENGTH:
            truncated += "... (truncated)"
        self.raw_response = raw_response
        super().__init__(
            f"Failed to parse LLM response into {model_name}: {cause}\nRaw response: {truncated}"
        )


_RETRYABLE_ERRORS = (
    openai.RateLimitError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,
)

_MAX_RETRIES = 3
_MAX_PARSE_RETRIES = 1


class LLMClient:
    """Thin wrapper around the OpenAI-compatible API for LLM calls."""

    def __init__(
        self,
        settings: Settings,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._model = settings.llm_model
        self._temperature = settings.llm_temperature
        self._top_p = settings.llm_top_p
        self._max_tokens = settings.llm_max_tokens
        self._rate_limiter = rate_limiter or create_rate_limiter(settings)
        self._client = openai.OpenAI(
            api_key=settings.llm_api_key.get_secret_value(),
            base_url=settings.resolved_llm_base_url,
            timeout=float(settings.request_timeout_seconds),
        )
        # Thread-safe cumulative token tracking
        self._usage_lock = threading.Lock()
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0
        self._llm_calls = 0

    def get_usage(self) -> TokenUsage:
        """Return cumulative token usage for all API calls made by this client."""
        with self._usage_lock:
            return TokenUsage(
                prompt_tokens=self._total_prompt_tokens,
                completion_tokens=self._total_completion_tokens,
                total_tokens=self._total_prompt_tokens + self._total_completion_tokens,
                llm_calls=self._llm_calls,
            )

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
    ) -> T:
        """Send a chat completion request and parse the response into a Pydantic model.

        Three-layer parsing strategy:
        1. Strip markdown fences and attempt direct parse.
        2. If that fails, try to extract JSON object from surrounding text.
        3. If extraction fails, retry the API call once (LLMs often self-correct).

        Transient API errors (rate limits, timeouts, server errors) are retried
        separately with exponential backoff and jitter.
        """
        schema_json = json.dumps(response_model.model_json_schema(), indent=2)
        system_with_schema = (
            f"{system_prompt}\n\n"
            "You MUST respond with valid JSON that conforms to this schema:\n"
            f"```json\n{schema_json}\n```\n"
            "Return ONLY the JSON object, no additional text."
        )

        last_error: Exception | None = None

        for attempt in range(_MAX_PARSE_RETRIES + 1):
            try:
                raw_content = self._call_api(system_with_schema, user_prompt)
            except LLMEmptyResponseError as err:
                last_error = err
                if attempt < _MAX_PARSE_RETRIES:
                    logger.warning(
                        "llm returned empty response, retrying",
                        attempt=attempt,
                    )
                    continue
                raise

            # Layer 1: strip markdown fences and parse directly
            cleaned = _strip_markdown_fences(raw_content)
            try:
                return response_model.model_validate_json(cleaned)
            except ValidationError as err:
                last_error = err
                logger.debug(
                    "direct json parse failed, trying extraction",
                    attempt=attempt,
                    error=str(err),
                )

            # Layer 2: extract JSON object from surrounding text
            extracted = _extract_json_object(raw_content)
            if extracted is not None:
                try:
                    return response_model.model_validate_json(extracted)
                except ValidationError as err:
                    last_error = err
                    logger.debug(
                        "extracted json validation failed",
                        attempt=attempt,
                        error=str(err),
                    )

            # Layer 3: retry the API call (next iteration of the loop)
            if attempt < _MAX_PARSE_RETRIES:
                logger.warning(
                    "llm returned unparseable response, retrying",
                    attempt=attempt,
                    raw_response=raw_content[:_MAX_RAW_LOG_LENGTH],
                )

        raise LLMResponseParseError(
            raw_response=raw_content,
            model_name=response_model.__name__,
            cause=last_error if last_error is not None else ValueError("unknown parse error"),
        )

    @retry(
        retry=retry_if_exception_type(_RETRYABLE_ERRORS),
        wait=wait_exponential_jitter(initial=1, max=30, jitter=1),
        stop=stop_after_attempt(_MAX_RETRIES + 1),
        before_sleep=before_sleep_log(logger, "WARNING"),  # type: ignore[arg-type]
    )
    def _call_api(self, system_prompt: str, user_prompt: str) -> str:
        """Make the actual API call with retry logic for transient errors."""
        self._rate_limiter.acquire()

        logger.debug(
            "sending llm request",
            model=self._model,
        )

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self._temperature,
                top_p=self._top_p,
                max_tokens=self._max_tokens,
            )
        except openai.RateLimitError as err:
            # Adapt rate limiter from provider feedback before re-raising
            # (tenacity will handle the retry)
            retry_after = getattr(err, "retry_after", None)
            if retry_after is not None:
                self._rate_limiter.update_from_retry_after(float(retry_after))
            raise

        if not response.choices:
            msg = "LLM returned response with no choices"
            raise LLMEmptyResponseError(msg)

        message = response.choices[0].message
        raw_content = message.content

        # NVIDIA nemotron-3-super may use reasoning_content for thinking,
        # leaving content as None when max_tokens is exhausted during reasoning.
        # Fall back to reasoning_content if content is empty.
        if not raw_content or not raw_content.strip():
            reasoning = getattr(message, "reasoning_content", None)
            if reasoning and reasoning.strip():
                logger.warning(
                    "llm returned empty content but has reasoning_content, "
                    "try increasing LLM_MAX_TOKENS",
                    finish_reason=response.choices[0].finish_reason,
                )
            msg = "LLM returned empty response"
            raise LLMEmptyResponseError(msg)

        usage = response.usage
        with self._usage_lock:
            self._llm_calls += 1
            if usage is not None:
                self._total_prompt_tokens += usage.prompt_tokens
                self._total_completion_tokens += usage.completion_tokens
            else:
                logger.warning("llm response missing usage data, token tracking may be inaccurate")

        if usage is not None:
            logger.info(
                "llm request completed",
                model=self._model,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
            )

        return raw_content


def _strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences that LLMs sometimes wrap around JSON."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        newline_idx = cleaned.find("\n")
        cleaned = cleaned[3:] if newline_idx == -1 else cleaned[newline_idx + 1 :]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


def _extract_json_object(text: str) -> str | None:
    """Try to find a JSON object in text that has extra content around it.

    Handles cases like:
        "Here is my analysis:\\n{\\\"findings\\\": [...], ...}"
    """
    match = _JSON_OBJECT_PATTERN.search(text)
    if match is None:
        return None
    candidate = match.group(0)
    try:
        json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return candidate
