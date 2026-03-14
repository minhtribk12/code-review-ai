# Architecture

## System Overview

```
CLI (Typer)
  -> Orchestrator
       -> [Security Agent]     \
       -> [Performance Agent]   |-- parallel execution (ThreadPoolExecutor)
       -> [Style Agent]         |
       -> [Test Coverage Agent]/
  -> Synthesizer (merges agent results into summary + risk level)
  -> Reporter (Rich terminal / Markdown)
```

## Pipeline Flow

```
CLI input (--diff or --pr)
    |
    v
ReviewInput ----------> Agent.review()
                              |
                              v
                        FindingsResponse  (from LLM)
                              |
                              v
                        AgentResult  (+ name, timing, status)
                              |
                    +---------+---------+
                    v                   v
              SynthesisResponse    agent_results
                    |                   |
                    +---------+---------+
                              v
                        ReviewReport --> Rich terminal / Markdown
```

## Component Responsibilities

### CLI (`main.py`)
- Parses `--pr` / `--diff` / `--output` / `--verbose` flags
- Validates input (mutual exclusion, file existence)
- Loads settings with friendly error messages
- Parses unified diff format (detects added/deleted/renamed/modified status)
- Orchestrates the full pipeline and handles top-level errors

### Orchestrator (`orchestrator.py`)
- Instantiates all 4 agents with shared LLM client
- Dispatches agent reviews in parallel via `ThreadPoolExecutor`
- Collects results with graceful degradation (failed agents don't crash pipeline)
- Runs synthesis step: one LLM call to merge all findings into overall assessment

### Base Agent (`agents/base.py`)
- Abstract base class using template method pattern
- `__init_subclass__` validates subclass contract at import time
- `review()` is the entry point: format prompt, call LLM, wrap result
- `_extra_context()` hook for agent-specific prompt additions
- `_format_user_prompt()` owns the prompt structure (not overridable)
- Structured error handling: LLM errors -> `AgentResult(status=FAILED)`

### LLM Client (`llm_client.py`)
- Thin wrapper around OpenAI-compatible API
- Schema injection: Pydantic JSON schema appended to system prompt
- Three-layer JSON parsing: fence strip -> extract from prose -> retry
- Tenacity retry for transient errors (rate limit, timeout, server error)
- Custom exceptions: `LLMResponseParseError`, `LLMEmptyResponseError`

### Models (`models.py`)
- All frozen (immutable) Pydantic v2 models
- `StrEnum` types for all constrained values (Severity, Confidence, AgentStatus, DiffStatus)
- `Finding` -> `FindingsResponse` -> `AgentResult` -> `ReviewReport` hierarchy
- Computed field `total_findings` aggregates counts across agents

### Report (`report.py`)
- Rich terminal: colored panels, severity tables, agent summaries
- Markdown: structured document for file export or PR comments
- Severity colors: critical=bold red, high=red, medium=yellow, low=green

## Design Decisions

### Why multi-agent instead of one big prompt?
- Specialized prompts produce better results than a single overloaded prompt
- Agents run in parallel (total time ~= 1 agent, not 4)
- Agents can be added/removed/tuned independently
- Graceful degradation: one failure doesn't lose everything

### Why ThreadPoolExecutor, not asyncio?
- Only 4 concurrent I/O-bound calls (LLM API)
- Threads release the GIL during I/O
- Simpler code: no async/await propagation
- OpenAI SDK sync client works natively with threads

### Why StrEnum instead of Literal?
- Reusable across models (Severity used in 4+ places)
- Iterable: `list(Severity)` for report rendering
- Central change: update one enum, everything follows
- Consistent rule: all constrained values use StrEnum

### Why schema injection instead of response_format?
- Works with ANY OpenAI-compatible provider
- Not all providers support structured output or tool calling
- Pydantic JSON schema is the single source of truth
- Markdown fence stripping handles common LLM output quirks

### Why tenacity for retry?
- Clean decorator-based API separates retry logic from business logic
- Built-in exponential backoff with jitter (prevents thundering herd)
- Zero dependencies (tenacity has no transitive deps)
- Logging hooks for observability
