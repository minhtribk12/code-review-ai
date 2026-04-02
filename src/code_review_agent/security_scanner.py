"""Security scanner: detect common security issues in diff content.

Scans for leaked secrets, unsafe code patterns, and injection vulnerabilities.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class ScanSeverity(StrEnum):
    """Severity levels for security findings."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class SecurityPattern:
    """A compiled security detection pattern."""

    name: str
    regex: re.Pattern[str]
    severity: ScanSeverity
    message: str
    file_extensions: frozenset[str] | None  # None = all files


@dataclass(frozen=True)
class SecurityFinding:
    """A single security finding from scanning."""

    pattern_name: str
    severity: ScanSeverity
    message: str
    file_path: str
    line_number: int | None
    matched_text: str


_SEVERITY_ORDER = {
    ScanSeverity.CRITICAL: 0,
    ScanSeverity.HIGH: 1,
    ScanSeverity.MEDIUM: 2,
    ScanSeverity.LOW: 3,
}

# Common false positive IPs to skip
_SAFE_IPS = frozenset({"127.0.0.1", "0.0.0.0", "255.255.255.255", "192.168.0.1", "10.0.0.0"})  # noqa: S104

DEFAULT_PATTERNS: tuple[SecurityPattern, ...] = (
    SecurityPattern(
        name="hardcoded_api_key",
        regex=re.compile(r"(?i)(api[_-]?key|apikey)\s*[=:]\s*['\"][A-Za-z0-9_\-]{20,}['\"]"),
        severity=ScanSeverity.CRITICAL,
        message="Hardcoded API key detected",
        file_extensions=None,
    ),
    SecurityPattern(
        name="hardcoded_secret",
        regex=re.compile(r"(?i)(secret|password|passwd|token)\s*[=:]\s*['\"][^'\"]{8,}['\"]"),
        severity=ScanSeverity.CRITICAL,
        message="Hardcoded secret or password detected",
        file_extensions=None,
    ),
    SecurityPattern(
        name="aws_access_key",
        regex=re.compile(r"AKIA[0-9A-Z]{16}"),
        severity=ScanSeverity.CRITICAL,
        message="AWS access key ID detected",
        file_extensions=None,
    ),
    SecurityPattern(
        name="private_key",
        regex=re.compile(r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----"),
        severity=ScanSeverity.CRITICAL,
        message="Private key detected",
        file_extensions=None,
    ),
    SecurityPattern(
        name="eval_usage",
        regex=re.compile(r"\beval\s*\("),
        severity=ScanSeverity.HIGH,
        message="Use of eval() is a code injection risk",
        file_extensions=frozenset({".py", ".js", ".ts"}),
    ),
    SecurityPattern(
        name="pickle_deserialize",
        regex=re.compile(r"pickle\.loads?\s*\("),
        severity=ScanSeverity.HIGH,
        message="Pickle deserialization can execute arbitrary code",
        file_extensions=frozenset({".py"}),
    ),
    SecurityPattern(
        name="innerHTML_xss",
        regex=re.compile(r"\.innerHTML\s*="),
        severity=ScanSeverity.HIGH,
        message="Direct innerHTML assignment is an XSS risk",
        file_extensions=frozenset({".js", ".ts", ".jsx", ".tsx"}),
    ),
    SecurityPattern(
        name="sql_string_format",
        regex=re.compile(
            r"(?i)(execute|cursor\.execute|query)\s*\(\s*f['\"]"
            r"|\.format\s*\(.*(?:SELECT|INSERT|UPDATE|DELETE)"
        ),
        severity=ScanSeverity.HIGH,
        message="SQL query built with string formatting is an injection risk",
        file_extensions=frozenset({".py"}),
    ),
    SecurityPattern(
        name="subprocess_shell",
        regex=re.compile(r"subprocess\.(call|run|Popen)\s*\([^)]*shell\s*=\s*True"),
        severity=ScanSeverity.MEDIUM,
        message="subprocess with shell=True can enable command injection",
        file_extensions=frozenset({".py"}),
    ),
    SecurityPattern(
        name="hardcoded_ip",
        regex=re.compile(
            r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}"
            r"(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
        ),
        severity=ScanSeverity.LOW,
        message="Hardcoded IP address detected",
        file_extensions=None,
    ),
)


def scan_text(
    content: str,
    file_path: str,
    patterns: tuple[SecurityPattern, ...] | None = None,
) -> list[SecurityFinding]:
    """Scan text content for security issues. Returns findings sorted by severity."""
    active_patterns = patterns or DEFAULT_PATTERNS
    ext = _get_extension(file_path)
    findings: list[SecurityFinding] = []

    for pattern in active_patterns:
        if pattern.file_extensions is not None and ext not in pattern.file_extensions:
            continue
        for i, line in enumerate(content.splitlines(), start=1):
            for match in pattern.regex.finditer(line):
                matched = match.group(0)
                # Skip known safe IPs
                if pattern.name == "hardcoded_ip" and matched in _SAFE_IPS:
                    continue
                findings.append(
                    SecurityFinding(
                        pattern_name=pattern.name,
                        severity=pattern.severity,
                        message=pattern.message,
                        file_path=file_path,
                        line_number=i,
                        matched_text=matched[:80],
                    )
                )

    return sorted(findings, key=lambda f: _SEVERITY_ORDER.get(f.severity, 99))


def scan_diff(
    diff_text: str,
    patterns: tuple[SecurityPattern, ...] | None = None,
) -> list[SecurityFinding]:
    """Scan a unified diff, only checking added lines."""
    all_findings: list[SecurityFinding] = []
    current_file = ""
    line_number = 0

    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
            continue
        if line.startswith("@@ "):
            # Parse hunk header for line number: @@ -old,count +new,count @@
            hunk_match = re.search(r"\+(\d+)", line)
            if hunk_match:
                line_number = int(hunk_match.group(1)) - 1
            continue
        if line.startswith("+") and not line.startswith("+++"):
            line_number += 1
            added_content = line[1:]
            for finding in scan_text(added_content, current_file, patterns):
                all_findings.append(
                    SecurityFinding(
                        pattern_name=finding.pattern_name,
                        severity=finding.severity,
                        message=finding.message,
                        file_path=current_file,
                        line_number=line_number,
                        matched_text=finding.matched_text,
                    )
                )
        elif not line.startswith("-"):
            line_number += 1

    return sorted(all_findings, key=lambda f: _SEVERITY_ORDER.get(f.severity, 99))


def format_scan_report(findings: list[SecurityFinding]) -> str:
    """Format findings as a readable report grouped by severity."""
    if not findings:
        return "  No security issues found."

    lines: list[str] = []
    current_severity = None
    for f in findings:
        if f.severity != current_severity:
            current_severity = f.severity
            lines.append(f"\n  [{current_severity.upper()}]")
        loc = f"{f.file_path}:{f.line_number}" if f.line_number else f.file_path
        lines.append(f"    {loc} - {f.message}")
        lines.append(f"      matched: {f.matched_text}")

    return "\n".join(lines)


def _get_extension(file_path: str) -> str:
    """Extract file extension from a path."""
    dot_idx = file_path.rfind(".")
    if dot_idx == -1:
        return ""
    return file_path[dot_idx:]
