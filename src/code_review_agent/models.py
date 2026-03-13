from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, computed_field


class Finding(BaseModel):
    """A single review finding from an agent."""

    model_config = {"frozen": True}

    severity: Literal["critical", "high", "medium", "low"]
    category: str
    title: str
    description: str
    file_path: str | None = None
    line_number: int | None = None
    suggestion: str | None = None


class AgentResult(BaseModel):
    """Result produced by a single review agent."""

    model_config = {"frozen": True}

    agent_name: str
    findings: list[Finding]
    summary: str
    execution_time_seconds: float


class ReviewReport(BaseModel):
    """Aggregated review report from all agents."""

    model_config = {"frozen": True}

    pr_url: str | None = None
    reviewed_at: datetime
    agent_results: list[AgentResult]
    overall_summary: str
    risk_level: Literal["low", "medium", "high", "critical"]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_findings(self) -> dict[str, int]:
        """Count findings grouped by severity."""
        counts: dict[str, int] = {
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
        }
        for result in self.agent_results:
            for finding in result.findings:
                counts[finding.severity] += 1
        return counts


class DiffFile(BaseModel):
    """A single file's diff content."""

    model_config = {"frozen": True}

    filename: str
    patch: str
    status: str


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
    risk_level: Literal["low", "medium", "high", "critical"]
