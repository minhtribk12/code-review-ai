"""Mock OpenAI-compatible LLM server for interactive CLI testing.

Responds to POST /v1/chat/completions with realistic code review findings.
Detects whether the request is from a review agent or the synthesis step
by inspecting the system prompt.

Run:
    uv run uvicorn interactive_tests.cli.mock_llm_server:app --port 9999
"""

from __future__ import annotations

import json
import time
import uuid

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Mock LLM Server")

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
# Mock responses per agent type
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


def _detect_agent(system_prompt: str) -> str:
    """Detect which agent is calling based on the system prompt content."""
    prompt_lower = system_prompt.lower()
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


_AGENT_RESPONSES: dict[str, str] = {
    "security": _SECURITY_RESPONSE,
    "performance": _PERFORMANCE_RESPONSE,
    "style": _STYLE_RESPONSE,
    "test_coverage": _TEST_COVERAGE_RESPONSE,
    "synthesis": _SYNTHESIS_RESPONSE,
}


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@app.post("/v1/chat/completions")
def chat_completions(request: ChatRequest) -> ChatResponse:
    """Handle chat completion requests with mock responses."""
    system_prompt = ""
    for msg in request.messages:
        if msg.role == "system":
            system_prompt = msg.content
            break

    agent = _detect_agent(system_prompt)
    response_content = _AGENT_RESPONSES.get(agent, _SECURITY_RESPONSE)

    # Simulate realistic latency
    time.sleep(0.3)

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
