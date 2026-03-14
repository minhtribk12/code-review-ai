# Data Models

## Model Hierarchy

```
Finding              -- single issue found by an agent
AgentResult          -- all findings from one agent + summary + timing + status
ReviewReport         -- all agent results + overall summary + risk level
DiffFile             -- one file's diff content
ReviewInput          -- all diffs + PR metadata (pipeline input)
FindingsResponse     -- LLM output contract for agent findings
SynthesisResponse    -- LLM output contract for orchestrator synthesis
```

## Enums

### Severity
Values: `critical`, `high`, `medium`, `low`

Used by: `Finding.severity`, `ReviewReport.risk_level`, `SynthesisResponse.risk_level`,
report rendering (colors, ordering), synthesis prompt guidelines.

### Confidence
Values: `high`, `medium`, `low`

Used by: `Finding.confidence`. Prepared for Phase 5 validation loop where low
confidence findings receive more scrutiny.

### AgentStatus
Values: `success`, `partial`, `failed`

Used by: `AgentResult.status`. Distinguishes "agent found nothing" from "agent
broke". `partial` reserved for Phase 5 deepening loop (round N fails, round 0
findings preserved).

### DiffStatus
Values: `added`, `modified`, `deleted`, `renamed`

Used by: `DiffFile.status`. Detected from git diff headers (`new file mode`,
`deleted file mode`, `rename from`) and `--- /dev/null` / `+++ /dev/null` lines.
GitHub API status strings are mapped via `_map_github_status()`.

## Finding

```python
class Finding(BaseModel):
    model_config = {"frozen": True}

    severity: Severity
    category: str              # "SQL Injection", "N+1 Query", etc.
    title: str                 # one-line summary
    description: str           # full explanation
    file_path: str | None      # which file (if identifiable)
    line_number: int | None    # which line (if identifiable)
    suggestion: str | None     # how to fix
    confidence: Confidence     # LLM's confidence (default: medium)
```

- `severity` uses `Literal` not `Enum` for simpler LLM JSON output
- Optional fields: not every finding can point to a specific line
- `confidence` defaults to `medium`, set by the LLM per finding

## AgentResult

```python
class AgentResult(BaseModel):
    model_config = {"frozen": True}

    agent_name: str
    findings: list[Finding]
    summary: str
    execution_time_seconds: float
    status: AgentStatus = AgentStatus.SUCCESS
    error_message: str | None = None
```

- Wraps `FindingsResponse` with metadata the LLM doesn't know
- `status` + `error_message`: structured error reporting instead of exceptions

## ReviewReport

```python
class ReviewReport(BaseModel):
    model_config = {"frozen": True}

    pr_url: str | None
    reviewed_at: datetime
    agent_results: list[AgentResult]
    overall_summary: str
    risk_level: Severity

    @computed_field
    def total_findings(self) -> dict[str, int]:
        # counts by severity, auto-updates from enum
```

- `risk_level` reuses `Severity` enum (same scale)
- `total_findings` computed from agent data (never stale)

## LLM Contracts

### FindingsResponse
```python
class FindingsResponse(BaseModel):
    findings: list[Finding]
    summary: str
```

### SynthesisResponse
```python
class SynthesisResponse(BaseModel):
    overall_summary: str
    risk_level: Severity
```

These define the exact JSON shape injected into the system prompt. The LLM
sees the Pydantic JSON schema and returns conforming output.
