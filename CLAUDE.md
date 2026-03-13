# Code Review Agent -- Project Instructions

## Project Overview

Multi-agent code review CLI tool that uses NVIDIA Nemotron 3 Super to run specialized agents
(security, performance, style, test coverage) that collaboratively review GitHub pull requests.
Built with Python, Typer, and Pydantic. Packaged with `uv`.

---

## Architecture

```
CLI (Typer)
  -> Orchestrator
       -> [Security Agent]     \
       -> [Performance Agent]   |-- parallel execution
       -> [Style Agent]         |
       -> [Test Coverage Agent]/
  -> Synthesizer (merges agent results)
  -> Reporter (outputs structured ReviewReport)
```

- **Orchestrator**: receives a code diff (from GitHub PR or local file), dispatches to all
  specialized agents in parallel, collects their structured findings.
- **Specialized Agents**: security, performance, style, test coverage. Each is a pure function
  that takes a prompt template + code diff and returns structured findings.
- **Synthesizer**: merges results from all agents, deduplicates, ranks by severity, produces a
  unified `ReviewReport`.
- **Reporter**: formats the final report for terminal (Rich) or JSON output.

---

## Agent Design Principles

- Each agent is a **pure function**: `(system_prompt, code_diff) -> list[Finding]`.
- Agents are **independent and parallelizable** -- no shared mutable state.
- Agent logic lives in `src/code_review_agent/agents/`, one module per agent.
- Agents do not call the LLM directly; they return a prompt payload that the orchestrator
  sends to the LLM client.
- All agent outputs are validated through Pydantic models before merging.

---

## LLM Provider Configuration

- Support multiple providers via **OpenAI-compatible API** (OpenRouter, NVIDIA API, Together,
  local servers).
- Provider selection is controlled by `LLM_PROVIDER` env var.
- Base URL mapping is maintained in `src/code_review_agent/config.py`.
- Default model: `nvidia/nemotron-3-super-120b-a12b`.
- Never hardcode API keys or secrets in code -- always load from environment.

---

## Prompt Engineering

- System prompts live in `src/code_review_agent/prompts/`, one file per agent:
  - `security.py` -- injection, auth, data exposure checks
  - `performance.py` -- algorithmic complexity, memory, I/O patterns
  - `style.py` -- naming, structure, readability, idiomatic patterns
  - `test_coverage.py` -- missing tests, edge cases, test quality
- Prompts are plain Python strings (not Jinja templates) for type safety and testability.
- Each prompt module exports a `build_prompt(diff: str) -> list[ChatMessage]` function.

---

## Output Format

- All agents return Pydantic models (`Finding`, `AgentResult`).
- The final report is a structured `ReviewReport` containing:
  - List of findings grouped by agent and severity
  - Summary statistics (critical/high/medium/low counts)
  - Overall risk assessment
- Output supports both Rich terminal rendering and JSON serialization.

---

## GitHub Integration

- Use **PyGithub** for fetching PR data (diff, file list, metadata).
- Support two input modes:
  1. PR URL: `code-review-agent review https://github.com/org/repo/pull/123`
  2. Local diff: `code-review-agent review --diff path/to/diff.patch`
- GitHub token is loaded from `GITHUB_TOKEN` env var.

---

## Testing

- Mock LLM responses using `respx` (for httpx-based calls).
- Test prompt formatting: verify correct system/user message structure.
- Test report generation: verify Pydantic model serialization and rendering.
- Test orchestrator: verify parallel dispatch and result merging.
- Test graceful degradation: simulate agent failures, verify partial reports.
- All tests must be deterministic -- no real API calls in unit tests.

---

## Configuration

- Use `pydantic-settings` for configuration management.
- `.env` file for API keys and provider settings (never committed).
- CLI flags (via Typer) override environment variables.
- Configuration hierarchy: CLI flags > env vars > `.env` file > defaults.

---

## Error Handling

- **Graceful degradation**: if one agent fails, the orchestrator still produces a partial
  report from the remaining agents.
- Failed agents are logged at WARNING level with context.
- The final report includes a `skipped_agents` field listing any agents that failed.
- LLM API errors (rate limits, timeouts) are retried with exponential backoff before failing.
- Never crash on a single agent failure -- always attempt to deliver value.

---

## Security

- No hardcoded API keys or secrets anywhere in the codebase.
- All secrets loaded from environment variables.
- `.env` is in `.gitignore` -- only `.env.example` is committed.
- API error responses are sanitized before logging (no tokens in logs).
- `detect-secrets` runs in pre-commit to catch accidental secret commits.
