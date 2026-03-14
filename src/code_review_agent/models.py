from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, computed_field


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


class ReviewReport(BaseModel):
    """Aggregated review report from all agents."""

    model_config = {"frozen": True}

    pr_url: str | None = None
    reviewed_at: datetime
    agent_results: list[AgentResult]
    overall_summary: str
    risk_level: Severity

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_findings(self) -> dict[str, int]:
        """Count findings grouped by severity."""
        counts: dict[str, int] = {s.value: 0 for s in Severity}
        for result in self.agent_results:
            for finding in result.findings:
                counts[finding.severity.value] += 1
        return counts


class ReviewEvent(StrEnum):
    """Events emitted by the orchestrator during a review."""

    AGENT_STARTED = "agent_started"
    AGENT_COMPLETED = "agent_completed"
    AGENT_FAILED = "agent_failed"
    SYNTHESIS_STARTED = "synthesis_started"
    SYNTHESIS_COMPLETED = "synthesis_completed"


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
