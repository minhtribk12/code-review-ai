"""Review comparison: compare two review runs to show resolved/new/persistent findings.

Matches findings by file+line+title (fuzzy) across two review runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from code_review_agent.storage import ReviewStorage

logger = structlog.get_logger(__name__)


class FindingStatus:
    """Status of a finding in the comparison."""

    RESOLVED = "resolved"  # was in old, not in new
    NEW = "new"  # not in old, is in new
    PERSISTENT = "persistent"  # in both old and new


@dataclass(frozen=True)
class ComparedFinding:
    """A finding with its comparison status."""

    title: str
    file_path: str | None
    line_number: int | None
    severity: str
    agent_name: str
    status: str  # FindingStatus value


@dataclass(frozen=True)
class ReviewComparison:
    """Result of comparing two reviews."""

    old_review_id: int
    new_review_id: int
    resolved: tuple[ComparedFinding, ...]
    new_findings: tuple[ComparedFinding, ...]
    persistent: tuple[ComparedFinding, ...]

    @property
    def resolved_count(self) -> int:
        return len(self.resolved)

    @property
    def new_count(self) -> int:
        return len(self.new_findings)

    @property
    def persistent_count(self) -> int:
        return len(self.persistent)


def compare_reviews(
    storage: ReviewStorage,
    old_review_id: int,
    new_review_id: int,
) -> ReviewComparison:
    """Compare two review runs and categorize findings."""
    old_findings = storage.load_findings_for_review(old_review_id)
    new_findings = storage.load_findings_for_review(new_review_id)

    old_keys = {_finding_key(f): f for f in old_findings}
    new_keys = {_finding_key(f): f for f in new_findings}

    resolved: list[ComparedFinding] = []
    new_list: list[ComparedFinding] = []
    persistent: list[ComparedFinding] = []

    # Findings in old but not in new = resolved
    for key, finding in old_keys.items():
        if key not in new_keys:
            resolved.append(_to_compared(finding, FindingStatus.RESOLVED))
        else:
            persistent.append(_to_compared(finding, FindingStatus.PERSISTENT))

    # Findings in new but not in old = new
    for key, finding in new_keys.items():
        if key not in old_keys:
            new_list.append(_to_compared(finding, FindingStatus.NEW))

    return ReviewComparison(
        old_review_id=old_review_id,
        new_review_id=new_review_id,
        resolved=tuple(resolved),
        new_findings=tuple(new_list),
        persistent=tuple(persistent),
    )


def format_comparison(comparison: ReviewComparison) -> str:
    """Format a review comparison as a readable report."""
    lines: list[str] = []
    lines.append(f"Review #{comparison.old_review_id} vs #{comparison.new_review_id}")
    lines.append(
        f"  {comparison.resolved_count} resolved, "
        f"{comparison.new_count} new, "
        f"{comparison.persistent_count} persistent"
    )

    if comparison.resolved:
        lines.append("\n  [RESOLVED]")
        for f in comparison.resolved:
            lines.append(f"    {f.severity} {f.title} ({f.file_path}:{f.line_number})")

    if comparison.new_findings:
        lines.append("\n  [NEW]")
        for f in comparison.new_findings:
            lines.append(f"    {f.severity} {f.title} ({f.file_path}:{f.line_number})")

    if comparison.persistent:
        lines.append("\n  [PERSISTENT]")
        for f in comparison.persistent:
            lines.append(f"    {f.severity} {f.title} ({f.file_path}:{f.line_number})")

    return "\n".join(lines)


def _finding_key(finding: dict[str, object]) -> tuple[str | None, int | None, str]:
    """Create a matching key from a finding dict."""
    fp = finding.get("file_path")
    ln = finding.get("line_number")
    return (
        str(fp) if fp else None,
        int(str(ln)) if ln else None,
        str(finding.get("title", "")),
    )


def _to_compared(finding: dict[str, object], status: str) -> ComparedFinding:
    """Convert a finding dict to a ComparedFinding."""
    fp = finding.get("file_path")
    ln = finding.get("line_number")
    return ComparedFinding(
        title=str(finding.get("title", "")),
        file_path=str(fp) if fp else None,
        line_number=int(str(ln)) if ln else None,
        severity=str(finding.get("severity", "medium")),
        agent_name=str(finding.get("agent_name", "")),
        status=status,
    )
