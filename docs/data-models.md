# Data Models

All models are defined in `src/code_review_agent/models.py`. Every model uses
`model_config = {"frozen": True}` (immutable after construction).

## Model Hierarchy

```
ReviewInput                          Pipeline input
  +-- DiffFile[]                     One file's patch + status
  +-- pr_url, pr_title, pr_description, fetch_warnings

FindingsResponse                     LLM contract: agent output
  +-- Finding[]                      Individual issues
  +-- summary

SynthesisResponse                    LLM contract: orchestrator synthesis
  +-- overall_summary
  +-- risk_level (Severity)

ValidationResponse                   LLM contract: validation output
  +-- ValidatedFinding[]             Findings with verdicts
  +-- false_positive_count
  +-- validation_summary

AgentResult                          Runtime wrapper around agent output
  +-- Finding[]
  +-- agent_name, execution_time_seconds, status, error_message

ReviewReport                         Final aggregated output
  +-- AgentResult[]
  +-- overall_summary, risk_level
  +-- token_usage (TokenUsage)
  +-- validation_result (ValidationResponse)
  +-- total_findings (computed)
```

## Enums

All enums inherit from `StrEnum` so they serialize as plain strings in JSON.

### Severity

Values: `critical`, `high`, `medium`, `low`

Used by: `Finding.severity`, `ReviewReport.risk_level`,
`SynthesisResponse.risk_level`, `ValidatedFinding.adjusted_severity`.
Also drives report rendering (colors, ordering) and synthesis prompt guidelines.

### Confidence

Values: `high`, `medium`, `low`

Used by: `Finding.confidence`. Low-confidence findings receive more scrutiny
during the validation step.

### AgentStatus

Values: `success`, `partial`, `failed`

Used by: `AgentResult.status`. Distinguishes "agent found nothing" from "agent
broke". `partial` covers cases where a deepening round fails but earlier-round
findings are preserved.

### DiffStatus

Values: `added`, `modified`, `deleted`, `renamed`

Used by: `DiffFile.status`. Detected from git diff headers (`new file mode`,
`deleted file mode`, `rename from`) and `--- /dev/null` / `+++ /dev/null` lines.
GitHub API status strings are mapped via `_map_github_status()`.

### OutputFormat

Values: `rich`, `json`

Controls the report rendering format.

### ReviewEvent

Values: `agent_started`, `agent_completed`, `agent_failed`,
`synthesis_started`, `synthesis_completed`, `validation_started`,
`validation_completed`

Events emitted by the orchestrator during a review. Used for progress reporting
and UI updates.

### ValidationVerdict

Values: `confirmed`, `likely_false_positive`, `uncertain`

Used by: `ValidatedFinding.verdict`. The validation agent assigns one of these
to each finding after cross-checking it against the diff context.

## Models

### Finding

A single review finding produced by an agent.

```python
class Finding(BaseModel):
    model_config = {"frozen": True}

    severity: Severity                           # required
    category: str                                # e.g. "SQL Injection", "N+1 Query"
    title: str                                   # one-line summary
    description: str                             # full explanation
    file_path: str | None = None                 # which file (if identifiable)
    line_number: int | None = None               # which line (if identifiable)
    suggestion: str | None = None                # how to fix
    confidence: Confidence = Confidence.MEDIUM   # LLM's confidence
```

- `severity` is a `Severity` StrEnum (serializes as a plain string in LLM JSON)
- Optional fields exist because not every finding can point to a specific file or line
- `confidence` defaults to `medium`

### AgentResult

Result produced by a single review agent. Wraps `FindingsResponse` with
runtime metadata the LLM does not know (timing, agent name, error state).

```python
class AgentResult(BaseModel):
    model_config = {"frozen": True}

    agent_name: str                              # required
    findings: list[Finding]                      # required
    summary: str                                 # required
    execution_time_seconds: float                # required
    status: AgentStatus = AgentStatus.SUCCESS
    error_message: str | None = None
```

- `status` + `error_message` provide structured error reporting instead of exceptions

### TokenUsage

Cumulative token usage for a review session.

```python
class TokenUsage(BaseModel):
    model_config = {"frozen": True}

    prompt_tokens: int                           # required
    completion_tokens: int                       # required
    total_tokens: int                            # required
    llm_calls: int                               # required
    estimated_cost_usd: float | None = None
```

### ValidatedFinding

A finding paired with a validation verdict from the validator agent.

```python
class ValidatedFinding(BaseModel):
    model_config = {"frozen": True}

    original_finding: Finding                    # required
    verdict: ValidationVerdict                   # required
    reasoning: str                               # required
    adjusted_severity: Severity | None = None
```

- `adjusted_severity` is set when the validator believes the original severity
  should be changed (e.g., downgraded after context analysis)

### ReviewReport

Aggregated review report from all agents. This is the final pipeline output.

```python
class ReviewReport(BaseModel):
    model_config = {"frozen": True}

    pr_url: str | None = None
    reviewed_at: datetime                        # required
    agent_results: list[AgentResult]             # required
    overall_summary: str                         # required
    risk_level: Severity                         # required
    fetch_warnings: list[str] = Field(default_factory=list)
    token_usage: TokenUsage | None = None
    rounds_completed: int = 1
    validation_result: ValidationResponse | None = None
```

**Computed field -- `total_findings`:**

```python
@computed_field
@property
def total_findings(self) -> dict[str, int]:
    """Count findings grouped by severity."""
    counts: dict[str, int] = {s.value: 0 for s in Severity}
    for result in self.agent_results:
        for finding in result.findings:
            counts[finding.severity.value] += 1
    return counts
```

Returns `{"critical": N, "high": N, "medium": N, "low": N}`. Always derived
from `agent_results`, never stale. Included in JSON serialization via
`@computed_field`.

### DiffFile

A single file's diff content.

```python
class DiffFile(BaseModel):
    model_config = {"frozen": True}

    filename: str                                # required
    patch: str                                   # required
    status: DiffStatus                           # required
```

### ReviewInput

Input data for the review pipeline.

```python
class ReviewInput(BaseModel):
    model_config = {"frozen": True}

    diff_files: list[DiffFile]                   # required
    pr_url: str | None = None
    pr_title: str | None = None
    pr_description: str | None = None
    fetch_warnings: list[str] = Field(default_factory=list)
```

## LLM Contracts

These models define the exact JSON shape the LLM must return. Their Pydantic
JSON schema is injected into the system prompt, and the LLM returns conforming
structured output.

### FindingsResponse

Returned by each review agent.

```python
class FindingsResponse(BaseModel):
    model_config = {"frozen": True}

    findings: list[Finding]                      # required
    summary: str                                 # required
```

### SynthesisResponse

Returned by the orchestrator synthesis step, which merges all agent results
into a single risk assessment.

```python
class SynthesisResponse(BaseModel):
    model_config = {"frozen": True}

    overall_summary: str                         # required
    risk_level: Severity                         # required
```

### ValidationResponse

Returned by the validation agent, which cross-checks findings against the diff
to filter false positives.

```python
class ValidationResponse(BaseModel):
    model_config = {"frozen": True}

    validated_findings: list[ValidatedFinding]   # required
    false_positive_count: int                    # required
    validation_summary: str                      # required
```
