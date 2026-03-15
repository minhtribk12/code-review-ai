from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, computed_field


class Severity(StrEnum):
    """Severity levels for findings and risk assessment."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Confidence(StrEnum):
    """Confidence levels for agent findings."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AgentStatus(StrEnum):
    """Status of an agent's review execution."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class Finding(BaseModel):
    """A single review finding from an agent."""

    model_config = {"frozen": True}

    severity: Severity
    category: str
    title: str
    description: str
    file_path: str | None = None
    line_number: int | None = None
    suggestion: str | None = None
    confidence: Confidence = Confidence.MEDIUM


class AgentResult(BaseModel):
    """Result produced by a single review agent."""

    model_config = {"frozen": True}

    agent_name: str
    findings: list[Finding]
    summary: str
    execution_time_seconds: float
    status: AgentStatus = AgentStatus.SUCCESS
    error_message: str | None = None


class TokenUsage(BaseModel):
    """Cumulative token usage for a review session."""

    model_config = {"frozen": True}

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    llm_calls: int
    estimated_cost_usd: float | None = None


class ValidationVerdict(StrEnum):
    """Verdict from the validation agent for a single finding."""

    CONFIRMED = "confirmed"
    LIKELY_FALSE_POSITIVE = "likely_false_positive"
    UNCERTAIN = "uncertain"


class ValidatedFinding(BaseModel):
    """A finding with a validation verdict from the validator agent."""

    model_config = {"frozen": True}

    original_finding: Finding
    verdict: ValidationVerdict
    reasoning: str
    adjusted_severity: Severity | None = None


class ValidationResponse(BaseModel):
    """LLM response model for the validation step."""

    model_config = {"frozen": True}

    validated_findings: list[ValidatedFinding]
    false_positive_count: int
    validation_summary: str


class ReviewReport(BaseModel):
    """Aggregated review report from all agents."""

    model_config = {"frozen": True}

    pr_url: str | None = None
    reviewed_at: datetime
    agent_results: list[AgentResult]
    overall_summary: str
    risk_level: Severity
    fetch_warnings: list[str] = Field(default_factory=list)
    token_usage: TokenUsage | None = None
    rounds_completed: int = 1
    validation_result: ValidationResponse | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_findings(self) -> dict[str, int]:
        """Count findings grouped by severity."""
        counts: dict[str, int] = {s.value: 0 for s in Severity}
        for result in self.agent_results:
            for finding in result.findings:
                counts[finding.severity.value] += 1
        return counts


class OutputFormat(StrEnum):
    """Output format for the review report."""

    RICH = "rich"
    JSON = "json"


class ReviewEvent(StrEnum):
    """Events emitted by the orchestrator during a review."""

    AGENT_STARTED = "agent_started"
    AGENT_COMPLETED = "agent_completed"
    AGENT_FAILED = "agent_failed"
    SYNTHESIS_STARTED = "synthesis_started"
    SYNTHESIS_COMPLETED = "synthesis_completed"
    VALIDATION_STARTED = "validation_started"
    VALIDATION_COMPLETED = "validation_completed"


class DiffStatus(StrEnum):
    """Status of a file in a diff."""

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"


class DiffFile(BaseModel):
    """A single file's diff content."""

    model_config = {"frozen": True}

    filename: str
    patch: str
    status: DiffStatus


class ReviewInput(BaseModel):
    """Input data for the review pipeline."""

    model_config = {"frozen": True}

    diff_files: list[DiffFile]
    pr_url: str | None = None
    pr_title: str | None = None
    pr_description: str | None = None
    fetch_warnings: list[str] = Field(default_factory=list)


class FindingsResponse(BaseModel):
    """LLM response model for agent findings extraction."""

    model_config = {"frozen": True}

    findings: list[Finding]
    summary: str


class SynthesisResponse(BaseModel):
    """LLM response model for the orchestrator synthesis step."""

    model_config = {"frozen": True}

    overall_summary: str
    risk_level: Severity
