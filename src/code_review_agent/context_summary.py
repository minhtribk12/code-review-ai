"""Context summarization for deepening rounds.

Instead of injecting full previous findings text into deepening rounds,
summarize them first to save tokens while preserving key information.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from code_review_agent.models import Finding

logger = structlog.get_logger(__name__)

_MAX_SUMMARY_FINDINGS = 20


def summarize_findings_for_deepening(findings: list[Finding]) -> str:
    """Summarize previous round findings for injection into next round.

    Instead of injecting full finding details (title + description +
    suggestion for each), produces a compact summary grouped by
    category with file:line references.
    """
    if not findings:
        return ""

    # Group by severity
    by_severity: dict[str, list[Finding]] = {}
    for f in findings[:_MAX_SUMMARY_FINDINGS]:
        sev = f.severity.value
        by_severity.setdefault(sev, []).append(f)

    lines = [
        f"Previous round found {len(findings)} issues:",
    ]

    for severity in ("critical", "high", "medium", "low"):
        group = by_severity.get(severity, [])
        if not group:
            continue

        lines.append(f"  {severity.upper()} ({len(group)}):")
        for f in group:
            loc = ""
            if f.file_path:
                loc = f" ({f.file_path}"
                if f.line_number:
                    loc += f":{f.line_number}"
                loc += ")"
            lines.append(f"    - {f.title}{loc}")

    lines.append("")
    lines.append("Focus on finding NEW issues not listed above. Do not re-report these findings.")

    summary = "\n".join(lines)
    logger.debug(
        "findings_summarized",
        original_count=len(findings),
        summary_chars=len(summary),
    )
    return summary


def estimate_token_savings(
    findings: list[Finding],
    summarized: str,
) -> tuple[int, int]:
    """Estimate token savings from summarization.

    Returns (full_tokens, summarized_tokens) based on ~4 chars per token.
    """
    full_text = ""
    for f in findings:
        full_text += f"{f.title} {f.description or ''} {f.suggestion or ''} "
        if f.file_path:
            full_text += f"{f.file_path} "
    full_tokens = len(full_text) // 4
    summary_tokens = len(summarized) // 4
    return full_tokens, summary_tokens
