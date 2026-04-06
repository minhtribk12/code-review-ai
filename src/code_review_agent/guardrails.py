"""Pre-finding guardrails: filter findings before they reach the user.

Inspired by DeerFlow's GuardrailMiddleware. Runs after agent execution
but before findings are displayed, removing noise and false positives.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from code_review_agent.models import Finding

logger = structlog.get_logger(__name__)

_TEST_FILE_PATTERNS = (
    re.compile(r"test[_/]"),
    re.compile(r"_test\.py$"),
    re.compile(r"\.test\.(ts|js|tsx|jsx)$"),
    re.compile(r"spec[_/]"),
    re.compile(r"_spec\.rb$"),
    re.compile(r"__tests__/"),
    re.compile(r"conftest\.py$"),
    re.compile(r"fixtures?[_/]"),
)

DEFAULT_CONFIDENCE_THRESHOLD = 0.3
DEFAULT_DISMISS_COUNT_THRESHOLD = 3


@dataclass(frozen=True)
class GuardrailResult:
    """Result of running guardrails on a findings list."""

    kept: list[Finding]
    filtered: list[FilteredFinding]


@dataclass(frozen=True)
class FilteredFinding:
    """A finding that was filtered out, with the reason."""

    finding: Finding
    reason: str


def apply_guardrails(
    findings: list[Finding],
    *,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    exclude_test_files: bool = True,
    suppressed_titles: set[str] | None = None,
    previous_titles: set[str] | None = None,
) -> GuardrailResult:
    """Filter findings through all guardrails.

    Args:
        findings: Raw findings from agents.
        confidence_threshold: Drop findings below this confidence.
        exclude_test_files: Skip findings in test files.
        suppressed_titles: Titles dismissed 3+ times (from triage history).
        previous_titles: Titles from the previous review (dedup).

    Returns:
        GuardrailResult with kept and filtered lists.
    """
    kept: list[Finding] = []
    filtered: list[FilteredFinding] = []
    suppressed = suppressed_titles or set()
    previous = previous_titles or set()

    for finding in findings:
        reason = _check_guardrails(
            finding,
            confidence_threshold=confidence_threshold,
            exclude_test_files=exclude_test_files,
            suppressed_titles=suppressed,
            previous_titles=previous,
        )
        if reason:
            filtered.append(FilteredFinding(finding=finding, reason=reason))
        else:
            kept.append(finding)

    if filtered:
        logger.info(
            "guardrails_applied",
            total=len(findings),
            kept=len(kept),
            filtered=len(filtered),
            reasons={r.reason for r in filtered},
        )

    return GuardrailResult(kept=kept, filtered=filtered)


def _check_guardrails(
    finding: Finding,
    *,
    confidence_threshold: float,
    exclude_test_files: bool,
    suppressed_titles: set[str],
    previous_titles: set[str],
) -> str | None:
    """Check a single finding against all guardrails. Returns reason or None."""
    # 1. Confidence threshold
    confidence_val = {"high": 0.9, "medium": 0.6, "low": 0.3}.get(finding.confidence.value, 0.5)
    if confidence_val < confidence_threshold:
        return f"low_confidence ({finding.confidence.value})"

    # 2. Test file exclusion
    if exclude_test_files and finding.file_path:
        for pattern in _TEST_FILE_PATTERNS:
            if pattern.search(finding.file_path):
                return f"test_file ({finding.file_path})"

    # 3. Known-suppressed patterns
    if finding.title in suppressed_titles:
        return f"suppressed ({finding.title[:40]})"

    # 4. Duplicate from previous review
    if finding.title in previous_titles:
        return f"duplicate_from_previous ({finding.title[:40]})"

    return None


def load_suppressed_titles(
    storage: object | None,
    threshold: int = DEFAULT_DISMISS_COUNT_THRESHOLD,
) -> set[str]:
    """Load titles dismissed N+ times from triage history."""
    if storage is None:
        return set()
    try:
        rows = storage.query_dismissed_patterns(min_count=threshold)  # type: ignore[attr-defined]
        return {row["title"] for row in rows}
    except Exception:
        return set()
