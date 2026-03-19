"""Map exceptions to structured UserError with reason and solution guidance.

Uses ``type(exc).__name__`` string matching to avoid import cycles with
domain modules (github_client, llm_client, etc.).
"""

from __future__ import annotations

from code_review_agent.errors import UserError


def classify_exception(exc: Exception, *, context: str = "") -> UserError:
    """Convert an arbitrary exception into a UserError with guidance.

    The *context* parameter provides domain info (e.g. ``"review"``,
    ``"GitHub API"``, ``"config"``) to improve the detail prefix.
    """
    exc_type = type(exc).__name__
    exc_msg = str(exc)
    prefix = f"{context}: " if context else ""

    # --- HTTP / GitHub errors ------------------------------------------------

    if exc_type == "GitHubAuthError":
        return UserError(
            detail=f"{prefix}GitHub authentication failed",
            reason="Your token is missing, expired, or lacks the required permissions.",
            solution=(
                "Set GITHUB_TOKEN in your .env file or environment. "
                "Ensure it has 'repo' scope for private repos. "
                "Generate a new token at github.com/settings/tokens."
            ),
        )

    if exc_type == "GitHubRateLimitExhausted":
        return UserError(
            detail=f"{prefix}GitHub API rate limit exhausted",
            reason="You have exceeded the allowed number of API requests.",
            solution=(
                "Wait for the rate limit to reset (usually ~1 hour), "
                "or authenticate with GITHUB_TOKEN for higher limits (5000/hr vs 60/hr)."
            ),
        )

    if exc_type == "HTTPStatusError":
        status = getattr(exc, "status_code", None) or _extract_status_code(exc_msg)
        return _classify_http_status(status, prefix, exc_msg)

    # --- LLM / OpenAI errors -------------------------------------------------

    if exc_type == "AuthenticationError":
        return UserError(
            detail=f"{prefix}LLM API authentication failed",
            reason="The API key is invalid, expired, or not set.",
            solution=(
                "Check your API key with 'config get llm_api_key'. "
                "Set it with 'config set llm_api_key <key>' or in your .env file."
            ),
        )

    if exc_type == "NotFoundError":
        return UserError(
            detail=f"{prefix}LLM model not found",
            reason=f"The configured model is not available on this provider: {exc_msg}",
            solution=(
                "Use 'provider models <name>' to see available models, "
                "or 'config set llm_model <model>' to switch."
            ),
        )

    if exc_type == "APIConnectionError":
        return UserError(
            detail=f"{prefix}Cannot reach LLM provider",
            reason="The API endpoint is unreachable (network issue or wrong URL).",
            solution=(
                "Check your network connection. "
                "Verify the base URL with 'config get llm_base_url'. "
                "Test connectivity with 'provider list'."
            ),
        )

    if exc_type == "APITimeoutError":
        return UserError(
            detail=f"{prefix}LLM request timed out",
            reason="The provider took too long to respond.",
            solution=(
                "The provider may be overloaded. Try again in a moment, "
                "or increase timeout with 'config set request_timeout_seconds 120'."
            ),
        )

    if exc_type == "RateLimitError":
        return UserError(
            detail=f"{prefix}LLM API rate limit reached",
            reason="Too many requests in a short time window.",
            solution=(
                "Wait a moment and retry. "
                "Reduce rate_limit_rpm with 'config set rate_limit_rpm <N>', "
                "or switch to another provider with 'provider list'."
            ),
        )

    if exc_type in ("InternalServerError", "APIStatusError"):
        return UserError(
            detail=f"{prefix}LLM provider error: {exc_msg}",
            reason="The LLM provider returned a server error.",
            solution="Retry in a few seconds. If persistent, try a different model or provider.",
        )

    # --- Parse / validation errors -------------------------------------------

    if exc_type == "LLMResponseParseError":
        return UserError(
            detail=f"{prefix}Failed to parse LLM response",
            reason="The model returned output that does not match the expected JSON schema.",
            solution=(
                "This is usually transient. Retry the review. "
                "If persistent, try a different model with 'config set llm_model <model>'."
            ),
        )

    if exc_type == "LLMEmptyResponseError":
        return UserError(
            detail=f"{prefix}LLM returned empty response",
            reason="The model generated no output (may have hit token limits).",
            solution=(
                "Try increasing max tokens with 'config set llm_max_tokens 4096'. "
                "If using a free tier, the model may be overloaded -- retry later."
            ),
        )

    if exc_type == "ValidationError":
        return UserError(
            detail=f"{prefix}Configuration validation failed",
            reason=exc_msg,
            solution="Check your .env file and config overrides with 'config validate'.",
        )

    # --- File / OS errors ----------------------------------------------------

    if exc_type == "FileNotFoundError":
        return UserError(
            detail=f"{prefix}File not found: {exc_msg}",
            reason="The specified path does not exist.",
            solution="Check the path and try again. Use Tab completion for suggestions.",
        )

    if exc_type == "PermissionError":
        return UserError(
            detail=f"{prefix}Permission denied: {exc_msg}",
            reason="Insufficient file system permissions.",
            solution="Check file permissions or run with appropriate access.",
        )

    # --- Git errors ----------------------------------------------------------

    if exc_type == "GitError":
        return UserError(
            detail=f"{prefix}{exc_msg}",
            reason="Git operation failed.",
            solution="Check 'status' for working tree state. Use 'help' for command syntax.",
        )

    # --- ValueError with known patterns --------------------------------------

    if exc_type == "ValueError":
        return _classify_value_error(prefix, exc_msg)

    # --- Fallback: unknown error ---------------------------------------------

    return UserError(
        detail=f"{prefix}{exc_msg}" if prefix else str(exc),
    )


def _classify_http_status(
    status: int | None,
    prefix: str,
    exc_msg: str,
) -> UserError:
    """Map HTTP status codes to guidance."""
    if status == 401 or status == 403:
        return UserError(
            detail=f"{prefix}Authentication or permission error (HTTP {status})",
            reason="The token is invalid or lacks required permissions.",
            solution=(
                "Check your GITHUB_TOKEN. For private repos, ensure it has 'repo' scope. "
                "Generate a new token at github.com/settings/tokens."
            ),
        )
    if status == 404:
        return UserError(
            detail=f"{prefix}Resource not found (HTTP 404)",
            reason="The repository, PR, or endpoint does not exist (or is private).",
            solution=(
                "Verify the owner/repo and PR number. "
                "Private repos require GITHUB_TOKEN with 'repo' scope."
            ),
        )
    if status == 422:
        return UserError(
            detail=f"{prefix}Request validation failed (HTTP 422)",
            reason="GitHub rejected the request parameters.",
            solution="Check branch names, PR state, and other parameters.",
        )
    if status == 429:
        return UserError(
            detail=f"{prefix}Rate limit exceeded (HTTP 429)",
            reason="Too many API requests.",
            solution="Wait for the rate limit to reset, or use an authenticated token.",
        )
    if status is not None and status >= 500:
        return UserError(
            detail=f"{prefix}Server error (HTTP {status})",
            reason="The remote server returned an internal error.",
            solution="Retry in a few seconds. If persistent, check status.github.com.",
        )

    return UserError(
        detail=f"{prefix}HTTP error: {exc_msg}",
    )


def _classify_value_error(prefix: str, exc_msg: str) -> UserError:
    """Map ValueError messages to guidance based on content patterns."""
    msg_lower = exc_msg.lower()

    if "api_key" in msg_lower or "api key" in msg_lower:
        return UserError(
            detail=f"{prefix}Missing API key",
            reason=exc_msg,
            solution=(
                "Set the API key in your .env file or with 'config edit'. "
                "Run 'cp .env.example .env' if no .env exists."
            ),
        )

    if "pr reference" in msg_lower or "pr not found" in msg_lower:
        return UserError(
            detail=f"{prefix}{exc_msg}",
            reason="The PR reference could not be parsed or the PR does not exist.",
            solution="Use format: owner/repo#123 or the full GitHub PR URL.",
        )

    if "no git remote" in msg_lower:
        return UserError(
            detail=f"{prefix}{exc_msg}",
            reason="No GitHub remote is configured for this repository.",
            solution="Use 'repo select owner/repo' to set a repo, or add a git remote.",
        )

    return UserError(detail=f"{prefix}{exc_msg}" if prefix else exc_msg)


def _extract_status_code(msg: str) -> int | None:
    """Try to extract an HTTP status code from an error message string."""
    import re

    match = re.search(r"\b(\d{3})\b", msg)
    if match:
        code = int(match.group(1))
        if 100 <= code <= 599:
            return code
    return None
