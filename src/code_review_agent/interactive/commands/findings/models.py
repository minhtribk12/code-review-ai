from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from code_review_agent.models import Confidence, Severity


class TriageAction(StrEnum):
    """Triage status for a finding."""

    OPEN = "open"
    SOLVED = "solved"
    FALSE_POSITIVE = "false_positive"
    IGNORED = "ignored"


class ViewerMode(StrEnum):
    """Current mode of the findings navigator TUI."""

    NAVIGATE = "navigate"
    DETAIL = "detail"
    FILTER = "filter"
    CONFIRM = "confirm"
    HELP = "help"


class FindingRow(BaseModel, frozen=True):
    """Single finding row displayed in the navigator table."""

    finding_db_id: int | None = None
    review_id: int | None = None
    index: int = 0
    severity: Severity = Severity.MEDIUM
    agent_name: str = ""
    category: str = ""
    title: str = ""
    description: str = ""
    file_path: str | None = None
    line_number: int | None = None
    suggestion: str | None = None
    confidence: Confidence = Confidence.MEDIUM
    repo: str | None = None
    pr_number: int | None = None
    triage_action: str = "open"
    is_posted: bool = False


class ActiveFilter(BaseModel, frozen=True):
    """A single active filter applied to the findings table."""

    field: str
    values: set[str]


# Column definitions: (key, label, weight, min_width)
_COLUMN_DEFS: list[tuple[str, str, float, int]] = [
    ("severity", "Sev", 0.05, 5),
    ("agent_name", "Agent", 0.10, 6),
    ("file_line", "File:Line", 0.22, 10),
    ("title", "Title", 0.20, 8),
    ("triage", "Status", 0.07, 6),
    ("pr_status", "PR", 0.06, 4),
    ("repo", "Repo", 0.12, 6),
    ("pr_number", "PR#", 0.04, 4),
    ("confidence", "Conf", 0.06, 4),
    ("category", "Category", 0.08, 6),
]

# Backward-compatible column list: (key, label, min_width)
_ALL_COLUMNS: list[tuple[str, str, int]] = [
    (key, label, min_width) for key, label, _weight, min_width in _COLUMN_DEFS
]

# Default visible column keys
_DEFAULT_VISIBLE: list[str] = [
    "severity",
    "agent_name",
    "file_line",
    "title",
    "triage",
    "pr_status",
]


class ConfirmAction(BaseModel, frozen=True):
    """An action awaiting user confirmation."""

    action: str
    description: str
    finding_row: FindingRow


# Severity sort order from most to least severe
_SEVERITY_ORDER: list[Severity] = [
    Severity.CRITICAL,
    Severity.HIGH,
    Severity.MEDIUM,
    Severity.LOW,
]
