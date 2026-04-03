"""Linter integration: run static analysis tools and merge with AI findings.

Supports configurable linters with built-in parsers for common tools.
Linter findings are merged into the findings navigator with source labels.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

import structlog

from code_review_agent.models import Confidence, Finding, Severity

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class LinterConfig:
    """Configuration for a single linter."""

    name: str
    command: str
    parser: str  # "ruff", "eslint", "mypy", "generic"
    timeout_seconds: int = 30


@dataclass(frozen=True)
class LinterResult:
    """Result from running a linter."""

    name: str
    findings: list[Finding]
    is_success: bool
    error_message: str = ""


def run_linter(config: LinterConfig) -> LinterResult:
    """Run a linter and parse its output into findings."""
    try:
        result = subprocess.run(  # noqa: S602 - linter commands are user-configured
            config.command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=config.timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return LinterResult(
            name=config.name,
            findings=[],
            is_success=False,
            error_message=f"Timed out after {config.timeout_seconds}s",
        )
    except Exception as exc:
        return LinterResult(
            name=config.name,
            findings=[],
            is_success=False,
            error_message=str(exc),
        )

    parser = _PARSERS.get(config.parser, _parse_generic)
    findings = parser(result.stdout, config.name)
    return LinterResult(
        name=config.name,
        findings=findings,
        is_success=True,
    )


def _parse_ruff(output: str, linter_name: str) -> list[Finding]:
    """Parse ruff JSON output."""
    try:
        entries = json.loads(output)
    except json.JSONDecodeError:
        return []
    if not isinstance(entries, list):
        return []

    findings: list[Finding] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        code = entry.get("code", "")
        message = entry.get("message", "")
        filename = entry.get("filename", "")
        location = entry.get("location", {})
        line = location.get("row") if isinstance(location, dict) else None

        findings.append(
            Finding(
                severity=_ruff_severity(code),
                category=f"[ruff] {code}",
                title=f"{code}: {message}",
                description=message,
                file_path=filename,
                line_number=line,
                suggestion=entry.get("fix", {}).get("message")
                if isinstance(entry.get("fix"), dict)
                else None,
                confidence=Confidence.HIGH,
            )
        )
    return findings


def _parse_eslint(output: str, linter_name: str) -> list[Finding]:
    """Parse eslint JSON output."""
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []

    findings: list[Finding] = []
    for file_entry in data:
        if not isinstance(file_entry, dict):
            continue
        file_path = file_entry.get("filePath", "")
        for msg in file_entry.get("messages", []):
            if not isinstance(msg, dict):
                continue
            findings.append(
                Finding(
                    severity=_eslint_severity(msg.get("severity", 1)),
                    category=f"[eslint] {msg.get('ruleId', '')}",
                    title=f"{msg.get('ruleId', 'unknown')}: {msg.get('message', '')}",
                    description=msg.get("message", ""),
                    file_path=file_path,
                    line_number=msg.get("line"),
                    confidence=Confidence.HIGH,
                )
            )
    return findings


def _parse_mypy(output: str, linter_name: str) -> list[Finding]:
    """Parse mypy JSON output (--output json)."""
    findings: list[Finding] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        findings.append(
            Finding(
                severity=_mypy_severity(entry.get("severity", "error")),
                category=f"[mypy] {entry.get('code', '')}",
                title=f"{entry.get('code', 'error')}: {entry.get('message', '')}",
                description=entry.get("message", ""),
                file_path=entry.get("file"),
                line_number=entry.get("line"),
                confidence=Confidence.HIGH,
            )
        )
    return findings


def _parse_generic(output: str, linter_name: str) -> list[Finding]:
    """Parse generic line-based output: file:line: message."""
    import re

    findings: list[Finding] = []
    for line in output.splitlines():
        match = re.match(r"(.+?):(\d+):?\s*(.+)", line)
        if match:
            findings.append(
                Finding(
                    severity=Severity.MEDIUM,
                    category=f"[{linter_name}]",
                    title=match.group(3).strip()[:80],
                    description=match.group(3).strip(),
                    file_path=match.group(1),
                    line_number=int(match.group(2)),
                    confidence=Confidence.MEDIUM,
                )
            )
    return findings


def merge_linter_findings(
    ai_findings: list[Finding],
    linter_findings: list[Finding],
) -> list[Finding]:
    """Merge linter findings with AI findings, deduplicating by file+line."""
    seen: set[tuple[str | None, int | None]] = set()
    for f in ai_findings:
        seen.add((f.file_path, f.line_number))

    merged = list(ai_findings)
    for f in linter_findings:
        key = (f.file_path, f.line_number)
        if key not in seen:
            merged.append(f)
            seen.add(key)
    return merged


def _ruff_severity(code: str) -> Severity:
    """Map ruff rule codes to severity levels."""
    if code.startswith(("S", "B")):
        return Severity.HIGH
    if code.startswith(("E", "W")):
        return Severity.MEDIUM
    return Severity.LOW


def _eslint_severity(level: int) -> Severity:
    """Map eslint severity (1=warning, 2=error) to our levels."""
    if level >= 2:
        return Severity.HIGH
    return Severity.MEDIUM


def _mypy_severity(severity: str) -> Severity:
    """Map mypy severity to our levels."""
    if severity == "error":
        return Severity.HIGH
    return Severity.MEDIUM


_PARSERS = {
    "ruff": _parse_ruff,
    "eslint": _parse_eslint,
    "mypy": _parse_mypy,
    "generic": _parse_generic,
}
