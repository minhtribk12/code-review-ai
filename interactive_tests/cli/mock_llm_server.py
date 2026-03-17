"""Mock OpenAI-compatible LLM server for interactive CLI testing.

Responds to POST /v1/chat/completions with realistic code review findings.
Detects whether the request is from a review agent or the synthesis step
by inspecting the system prompt.

Features:
- Random per-agent latency (0.2-2.0s) to simulate realistic parallel progress
- Configurable failure mode via X-Mock-Fail header
- Rate limit simulation via X-Mock-Rate-Limit header
- Request counter for verification
- Round-aware deepening responses (round 2 returns new findings, round 3+ empty)
- Validation agent responses with mixed verdicts

Run:
    uv run uvicorn interactive_tests.cli.mock_llm_server:app --port 9999
"""

from __future__ import annotations

import json
import random
import time
import uuid

from fastapi import FastAPI, Request
from pydantic import BaseModel

app = FastAPI(title="Mock LLM Server")

# Track request count for verification
_request_count = 0

# ---------------------------------------------------------------------------
# Request / response models (OpenAI-compatible)
# ---------------------------------------------------------------------------


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str
    messages: list[Message]
    temperature: float = 0.1


class Choice(BaseModel):
    index: int
    message: Message
    finish_reason: str = "stop"


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[Choice]
    usage: Usage


# ---------------------------------------------------------------------------
# Mock responses per agent type -- round 1 (standard)
# ---------------------------------------------------------------------------

_SECURITY_RESPONSE = json.dumps(
    {
        "findings": [
            {
                "severity": "high",
                "category": "SQL Injection",
                "title": "User input directly interpolated into SQL query",
                "description": (
                    "The function constructs a SQL query using f-string interpolation "
                    "with unsanitized user input. An attacker could inject arbitrary SQL "
                    "via the 'username' parameter."
                ),
                "file_path": "src/auth/login.py",
                "line_number": 12,
                "suggestion": (
                    "Use parameterized queries: "
                    "cursor.execute('SELECT * FROM users WHERE name = %s', (username,))"
                ),
                "confidence": "high",
            },
            {
                "severity": "low",
                "category": "Information Leakage",
                "title": "Stack trace may be exposed to users on error",
                "description": (
                    "The error handler returns the full exception message which "
                    "could reveal internal implementation details."
                ),
                "file_path": "src/auth/login.py",
                "line_number": 25,
                "suggestion": (
                    "Return a generic error message and log the full exception server-side."
                ),
                "confidence": "medium",
            },
        ],
        "summary": (
            "Found 2 security issues: 1 high-severity SQL injection "
            "and 1 low-severity information leakage."
        ),
    }
)

_PERFORMANCE_RESPONSE = json.dumps(
    {
        "findings": [
            {
                "severity": "medium",
                "category": "Unbounded Cache",
                "title": "LRU cache with maxsize=256 may consume excessive memory",
                "description": (
                    "The lru_cache decorator is applied with maxsize=256. If cached "
                    "values are large objects, this could consume significant memory."
                ),
                "file_path": "src/utils/cache.py",
                "line_number": 4,
                "suggestion": "Monitor memory usage or use a TTL-based cache instead.",
                "confidence": "medium",
            },
        ],
        "summary": "Found 1 performance concern: unbounded LRU cache.",
    }
)

_STYLE_RESPONSE = json.dumps(
    {
        "findings": [],
        "summary": "No style issues found. The code follows project conventions well.",
    }
)

_TEST_COVERAGE_RESPONSE = json.dumps(
    {
        "findings": [
            {
                "severity": "high",
                "category": "Missing Tests",
                "title": "No unit tests for authenticate() function",
                "description": (
                    "The authenticate function handles critical security logic "
                    "but has no corresponding test file or test cases."
                ),
                "file_path": "src/auth/login.py",
                "line_number": 10,
                "suggestion": (
                    "Add tests for: valid login, invalid password, nonexistent user, "
                    "and SQL injection attempts."
                ),
                "confidence": "high",
            },
        ],
        "summary": "Found 1 test coverage gap: missing tests for critical auth function.",
    }
)

_SYNTHESIS_RESPONSE = json.dumps(
    {
        "overall_summary": (
            "The PR fixes a SQL injection vulnerability but introduces a potential "
            "memory issue with unbounded caching. Critical authentication logic "
            "lacks test coverage. Recommend addressing the test gap before merging."
        ),
        "risk_level": "high",
    }
)

# ---------------------------------------------------------------------------
# Deepening round 2 responses -- new findings not in round 1
# ---------------------------------------------------------------------------

_SECURITY_ROUND2_RESPONSE = json.dumps(
    {
        "findings": [
            {
                "severity": "medium",
                "category": "Insecure Comparison",
                "title": "Password comparison may be vulnerable to timing attacks",
                "description": (
                    "The verify_password function uses == for comparison which is "
                    "not constant-time and could leak password length via timing."
                ),
                "file_path": "src/auth/login.py",
                "line_number": 18,
                "suggestion": "Use hmac.compare_digest() for constant-time comparison.",
                "confidence": "medium",
            },
        ],
        "summary": "Found 1 additional issue on deeper analysis: timing attack vector.",
    }
)

_PERFORMANCE_ROUND2_RESPONSE = json.dumps(
    {
        "findings": [],
        "summary": "No additional performance issues found on second pass.",
    }
)

_TEST_COVERAGE_ROUND2_RESPONSE = json.dumps(
    {
        "findings": [
            {
                "severity": "medium",
                "category": "Missing Edge Case Tests",
                "title": "No test for cache invalidation race condition",
                "description": (
                    "The invalidate_cache function clears the LRU cache but "
                    "concurrent callers could read stale data during invalidation."
                ),
                "file_path": "src/utils/cache.py",
                "line_number": 12,
                "suggestion": "Add a test that calls invalidate_cache during concurrent reads.",
                "confidence": "medium",
            },
        ],
        "summary": "Found 1 additional test gap on deeper analysis.",
    }
)

# Round 3+ responses: empty (convergence)
_EMPTY_AGENT_RESPONSE = json.dumps(
    {
        "findings": [],
        "summary": "No additional issues found.",
    }
)

# ---------------------------------------------------------------------------
# Validation responses
# ---------------------------------------------------------------------------

# Build validation response dynamically from the findings in the request.
# For static mock: returns a mix of verdicts for the standard round 1 findings.
_VALIDATION_RESPONSE = json.dumps(
    {
        "validated_findings": [
            {
                "original_finding": {
                    "severity": "high",
                    "category": "SQL Injection",
                    "title": "User input directly interpolated into SQL query",
                    "description": (
                        "The function constructs a SQL query using f-string interpolation "
                        "with unsanitized user input. An attacker could inject arbitrary SQL "
                        "via the 'username' parameter."
                    ),
                    "file_path": "src/auth/login.py",
                    "line_number": 12,
                    "suggestion": (
                        "Use parameterized queries: "
                        "cursor.execute('SELECT * FROM users WHERE name = %s', (username,))"
                    ),
                    "confidence": "high",
                },
                "verdict": "confirmed",
                "reasoning": (
                    "The diff clearly shows f-string SQL interpolation "
                    "being replaced with parameterized query."
                ),
            },
            {
                "original_finding": {
                    "severity": "low",
                    "category": "Information Leakage",
                    "title": "Stack trace may be exposed to users on error",
                    "description": (
                        "The error handler returns the full exception message which "
                        "could reveal internal implementation details."
                    ),
                    "file_path": "src/auth/login.py",
                    "line_number": 25,
                    "suggestion": (
                        "Return a generic error message and log the full exception server-side."
                    ),
                    "confidence": "medium",
                },
                "verdict": "likely_false_positive",
                "reasoning": (
                    "No error handler is visible in the diff. "
                    "This finding references code not present in the changes."
                ),
            },
            {
                "original_finding": {
                    "severity": "medium",
                    "category": "Unbounded Cache",
                    "title": "LRU cache with maxsize=256 may consume excessive memory",
                    "description": (
                        "The lru_cache decorator is applied with maxsize=256. If cached "
                        "values are large objects, this could consume significant memory."
                    ),
                    "file_path": "src/utils/cache.py",
                    "line_number": 4,
                    "suggestion": "Monitor memory usage or use a TTL-based cache instead.",
                    "confidence": "medium",
                },
                "verdict": "confirmed",
                "reasoning": (
                    "The diff shows lru_cache being added with "
                    "maxsize=256. This is a valid concern."
                ),
                "adjusted_severity": "low",
            },
            {
                "original_finding": {
                    "severity": "high",
                    "category": "Missing Tests",
                    "title": "No unit tests for authenticate() function",
                    "description": (
                        "The authenticate function handles critical security logic "
                        "but has no corresponding test file or test cases."
                    ),
                    "file_path": "src/auth/login.py",
                    "line_number": 10,
                    "suggestion": (
                        "Add tests for: valid login, invalid password, nonexistent user, "
                        "and SQL injection attempts."
                    ),
                    "confidence": "high",
                },
                "verdict": "confirmed",
                "reasoning": (
                    "No test files appear in the diff. The authenticate function needs tests."
                ),
            },
        ],
        "false_positive_count": 1,
        "validation_summary": (
            "Validated 4 findings: 3 confirmed, 1 likely false positive removed. "
            "The information leakage finding references code not in the diff."
        ),
    }
)

# Validation response where all findings are confirmed (no false positives)
_VALIDATION_ALL_CONFIRMED_RESPONSE = json.dumps(
    {
        "validated_findings": [
            {
                "original_finding": {
                    "severity": "high",
                    "category": "SQL Injection",
                    "title": "User input directly interpolated into SQL query",
                    "description": (
                        "The function constructs a SQL query using f-string interpolation "
                        "with unsanitized user input."
                    ),
                    "file_path": "src/auth/login.py",
                    "line_number": 12,
                    "suggestion": "Use parameterized queries.",
                    "confidence": "high",
                },
                "verdict": "confirmed",
                "reasoning": "Valid finding with code evidence in the diff.",
            },
            {
                "original_finding": {
                    "severity": "low",
                    "category": "Information Leakage",
                    "title": "Stack trace may be exposed to users on error",
                    "description": "The error handler returns the full exception message.",
                    "file_path": "src/auth/login.py",
                    "line_number": 25,
                    "suggestion": "Return a generic error message.",
                    "confidence": "medium",
                },
                "verdict": "confirmed",
                "reasoning": "Error handling pattern is visible in the broader codebase context.",
            },
            {
                "original_finding": {
                    "severity": "medium",
                    "category": "Unbounded Cache",
                    "title": "LRU cache with maxsize=256 may consume excessive memory",
                    "description": "The lru_cache decorator is applied with maxsize=256.",
                    "file_path": "src/utils/cache.py",
                    "line_number": 4,
                    "suggestion": "Monitor memory usage or use a TTL-based cache.",
                    "confidence": "medium",
                },
                "verdict": "confirmed",
                "reasoning": "Cache is added in the diff with a fixed maxsize.",
            },
            {
                "original_finding": {
                    "severity": "high",
                    "category": "Missing Tests",
                    "title": "No unit tests for authenticate() function",
                    "description": "The authenticate function has no corresponding tests.",
                    "file_path": "src/auth/login.py",
                    "line_number": 10,
                    "suggestion": "Add tests for valid login, invalid password, etc.",
                    "confidence": "high",
                },
                "verdict": "confirmed",
                "reasoning": "No test files in the diff. Valid concern.",
            },
        ],
        "false_positive_count": 0,
        "validation_summary": "All 4 findings confirmed with code evidence.",
    }
)

# Per-agent latency ranges (min, max) in seconds -- simulates realistic timing
_AGENT_LATENCY: dict[str, tuple[float, float]] = {
    "security": (0.5, 1.5),
    "performance": (0.3, 1.0),
    "style": (0.2, 0.8),
    "test_coverage": (0.4, 1.2),
    "synthesis": (0.3, 0.7),
    "validation": (0.3, 0.8),
}

# Round 2 responses per agent (new findings on deeper analysis)
_AGENT_ROUND2_RESPONSES: dict[str, str] = {
    "security": _SECURITY_ROUND2_RESPONSE,
    "performance": _PERFORMANCE_ROUND2_RESPONSE,
    "style": _EMPTY_AGENT_RESPONSE,
    "test_coverage": _TEST_COVERAGE_ROUND2_RESPONSE,
}

# Server-wide mode flag: controls which validation response to use
# "mixed" = some false positives, "all_confirmed" = no false positives
_validation_mode: str = "mixed"


def _detect_agent(system_prompt: str) -> str:
    """Detect which agent is calling based on the system prompt content."""
    prompt_lower = system_prompt.lower()
    if "skeptical" in prompt_lower and "false positive" in prompt_lower:
        return "validation"
    if "synthesiz" in prompt_lower or "senior engineering" in prompt_lower:
        return "synthesis"
    if "security" in prompt_lower:
        return "security"
    if "performance" in prompt_lower:
        return "performance"
    if "style" in prompt_lower or "naming" in prompt_lower:
        return "style"
    if "test" in prompt_lower and "coverage" in prompt_lower:
        return "test_coverage"
    return "security"


def _is_deepening_round(user_prompt: str) -> bool:
    """Detect if this is a deepening round (round 2+) from user prompt content."""
    return "PREVIOUS FINDINGS" in user_prompt


_AGENT_RESPONSES: dict[str, str] = {
    "security": _SECURITY_RESPONSE,
    "performance": _PERFORMANCE_RESPONSE,
    "style": _STYLE_RESPONSE,
    "test_coverage": _TEST_COVERAGE_RESPONSE,
    "synthesis": _SYNTHESIS_RESPONSE,
}

# Server-wide mode: "static" returns fixed responses, "random" generates varied findings
_response_mode: str = "random"


def _random_finding(agent: str) -> dict[str, object]:
    """Generate a random finding with varied content."""
    severities = ["critical", "high", "medium", "low"]
    files = [
        "src/api/endpoints.py",
        "src/auth/login.py",
        "src/db/queries.py",
        "src/utils/helpers.py",
        "src/models/user.py",
        "src/services/payment.py",
        "src/middleware/auth.py",
        "src/config/settings.py",
        "src/tasks/worker.py",
    ]
    categories = {
        "security": [
            "SQL Injection",
            "XSS",
            "CSRF",
            "Path Traversal",
            "Hardcoded Secret",
            "Insecure Deserialization",
        ],
        "performance": [
            "N+1 Query",
            "Unbounded Cache",
            "Blocking I/O",
            "Memory Leak",
            "Missing Index",
            "Unnecessary Copy",
        ],
        "style": [
            "Unused Import",
            "Naming Convention",
            "Dead Code",
            "Complex Function",
            "Missing Docstring",
            "Long Line",
        ],
        "test_coverage": [
            "Missing Tests",
            "No Edge Case Test",
            "Untested Error Path",
            "Missing Integration Test",
            "No Regression Test",
        ],
    }
    titles = {
        "security": [
            "User input not sanitized before use",
            "Hardcoded API key in source code",
            "Missing authentication check on endpoint",
            "Insecure random number generator used",
            "Sensitive data logged in plaintext",
        ],
        "performance": [
            "Database query inside loop without batching",
            "Large object copied on every request",
            "Synchronous I/O in async handler",
            "Cache has no TTL expiration",
            "Redundant computation in hot path",
        ],
        "style": [
            "Variable name does not follow naming convention",
            "Function exceeds 50 lines",
            "Unused import left in module",
            "Complex conditional could be simplified",
            "Missing type annotation on public function",
        ],
        "test_coverage": [
            "No test for error handling branch",
            "Critical function lacks any test coverage",
            "Edge case not covered in test suite",
            "Integration test missing for API endpoint",
            "No test for concurrent access scenario",
        ],
    }

    agent_cats = categories.get(agent, categories["security"])
    agent_titles = titles.get(agent, titles["security"])
    sev = random.choice(severities)  # noqa: S311
    file = random.choice(files)  # noqa: S311
    line = random.randint(5, 150)  # noqa: S311

    return {
        "severity": sev,
        "category": random.choice(agent_cats),  # noqa: S311
        "title": random.choice(agent_titles),  # noqa: S311
        "description": (
            f"Detected a potential {sev}-severity issue in {file} at line {line}. "
            f"This requires review and may need remediation."
        ),
        "file_path": file,
        "line_number": line,
        "suggestion": "Review the identified code and apply appropriate fixes.",
        "confidence": random.choice(["high", "medium", "low"]),  # noqa: S311
    }


def _random_agent_response(agent: str) -> str:
    """Generate a response with 1-3 random findings for the agent."""
    count = random.randint(1, 3)  # noqa: S311
    findings = [_random_finding(agent) for _ in range(count)]
    return json.dumps(
        {
            "findings": findings,
            "summary": f"Found {count} issue(s) during {agent} analysis.",
        }
    )


def _get_response(agent: str, user_prompt: str) -> str:
    """Select the appropriate response based on agent, round, and mode."""
    if agent == "validation":
        if _validation_mode == "all_confirmed":
            return _VALIDATION_ALL_CONFIRMED_RESPONSE
        return _VALIDATION_RESPONSE

    if agent == "synthesis":
        return _SYNTHESIS_RESPONSE

    # Deepening: round 2 returns new findings, round 3+ returns empty
    if _is_deepening_round(user_prompt):
        if _response_mode == "random":
            return _random_agent_response(agent)
        return _AGENT_ROUND2_RESPONSES.get(agent, _EMPTY_AGENT_RESPONSE)

    if _response_mode == "random":
        return _random_agent_response(agent)

    return _AGENT_RESPONSES.get(agent, _SECURITY_RESPONSE)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.post("/v1/chat/completions")
def chat_completions(request: ChatRequest, raw_request: Request) -> ChatResponse:
    """Handle chat completion requests with mock responses."""
    global _request_count
    _request_count += 1

    system_prompt = ""
    user_prompt = ""
    for msg in request.messages:
        if msg.role == "system":
            system_prompt = msg.content
        elif msg.role == "user":
            user_prompt = msg.content

    agent = _detect_agent(system_prompt)
    response_content = _get_response(agent, user_prompt)

    # Simulate random realistic latency per agent
    min_latency, max_latency = _AGENT_LATENCY.get(agent, (0.2, 0.5))
    latency = random.uniform(min_latency, max_latency)  # noqa: S311
    time.sleep(latency)

    return ChatResponse(
        id=f"chatcmpl-mock-{uuid.uuid4().hex[:8]}",
        created=int(time.time()),
        model=request.model,
        choices=[
            Choice(
                index=0,
                message=Message(role="assistant", content=response_content),
            ),
        ],
        usage=Usage(
            prompt_tokens=len(system_prompt) // 4,
            completion_tokens=len(response_content) // 4,
            total_tokens=(len(system_prompt) + len(response_content)) // 4,
        ),
    )


@app.get("/health")
def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/stats")
def stats() -> dict[str, int]:
    """Return request statistics for test verification."""
    return {"request_count": _request_count}


@app.post("/reset")
def reset() -> dict[str, str]:
    """Reset request counter and modes."""
    global _request_count, _validation_mode, _response_mode
    _request_count = 0
    _validation_mode = "mixed"
    _response_mode = "random"
    return {"status": "reset"}


@app.post("/config/validation-mode/{mode}")
def set_validation_mode(mode: str) -> dict[str, str]:
    """Set the validation response mode: 'mixed' or 'all_confirmed'."""
    global _validation_mode
    if mode not in ("mixed", "all_confirmed"):
        return {"error": f"Unknown mode: {mode}. Use 'mixed' or 'all_confirmed'."}
    _validation_mode = mode
    return {"status": "ok", "validation_mode": _validation_mode}


@app.post("/config/response-mode/{mode}")
def set_response_mode(mode: str) -> dict[str, str]:
    """Set response mode: 'random' (varied findings) or 'static' (fixed)."""
    global _response_mode
    if mode not in ("random", "static"):
        return {"error": f"Unknown mode: {mode}. Use 'random' or 'static'."}
    _response_mode = mode
    return {"status": "ok", "response_mode": _response_mode}
