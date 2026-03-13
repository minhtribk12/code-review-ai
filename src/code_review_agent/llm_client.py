from __future__ import annotations

import json
from typing import TYPE_CHECKING, TypeVar

import openai
import structlog
from pydantic import BaseModel

if TYPE_CHECKING:
    from code_review_agent.config import Settings

logger = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMClient:
    """Thin wrapper around the OpenAI-compatible API for LLM calls."""

    def __init__(self, settings: Settings) -> None:
        self._model = settings.llm_model
        self._client = openai.OpenAI(
            api_key=settings.llm_api_key.get_secret_value(),
            base_url=settings.llm_base_url,
        )

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
    ) -> T:
        """Send a chat completion request and parse the response into a Pydantic model.

        The LLM is instructed to respond with valid JSON matching the schema of
        ``response_model``.  The raw response text is then validated through
        Pydantic for type-safe structured output.
        """
        schema_json = json.dumps(response_model.model_json_schema(), indent=2)
        system_with_schema = (
            f"{system_prompt}\n\n"
            "You MUST respond with valid JSON that conforms to this schema:\n"
            f"```json\n{schema_json}\n```\n"
            "Return ONLY the JSON object, no additional text."
        )

        logger.debug(
            "sending llm request",
            model=self._model,
            response_model=response_model.__name__,
        )

        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_with_schema},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
        )

        raw_content = response.choices[0].message.content or "{}"

        # Log token usage without leaking prompts or keys.
        usage = response.usage
        if usage is not None:
            logger.info(
                "llm request completed",
                model=self._model,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
            )

        # Strip markdown fences if the model wraps its response.
        cleaned = raw_content.strip()
        if cleaned.startswith("```"):
            first_newline = cleaned.index("\n")
            cleaned = cleaned[first_newline + 1 :]
        if cleaned.endswith("```"):
            cleaned = cleaned[: -len("```")]
        cleaned = cleaned.strip()

        return response_model.model_validate_json(cleaned)
