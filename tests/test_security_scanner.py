"""Tests for the security scanner."""

from __future__ import annotations

from code_review_agent.security_scanner import (
    ScanSeverity,
    SecurityFinding,
    format_scan_report,
    scan_diff,
    scan_text,
)


class TestScanText:
    """Test scanning text content for security issues."""

    def test_no_matches(self) -> None:
        assert scan_text("normal code here", "test.py") == []

    def test_detects_api_key(self) -> None:
        code = "API_KEY = 'sk-1234567890abcdefghij'"  # pragma: allowlist secret
        findings = scan_text(code, "config.py")
        assert len(findings) >= 1
        assert findings[0].pattern_name == "hardcoded_api_key"
        assert findings[0].severity == ScanSeverity.CRITICAL

    def test_detects_hardcoded_secret(self) -> None:
        code = 'password = "my_super_secret_password"'  # pragma: allowlist secret
        findings = scan_text(code, "app.py")
        assert any(f.pattern_name == "hardcoded_secret" for f in findings)

    def test_detects_aws_key(self) -> None:
        code = "aws_key = AKIAIOSFODNN7EXAMPLE"  # pragma: allowlist secret
        findings = scan_text(code, "deploy.sh")
        assert any(f.pattern_name == "aws_access_key" for f in findings)

    def test_detects_private_key(self) -> None:
        code = "-----BEGIN RSA PRIVATE KEY-----"  # pragma: allowlist secret
        findings = scan_text(code, "key.pem")
        assert any(f.pattern_name == "private_key" for f in findings)

    def test_detects_eval(self) -> None:
        findings = scan_text("result = eval(user_input)", "app.py")
        assert any(f.pattern_name == "eval_usage" for f in findings)

    def test_detects_pickle(self) -> None:
        findings = scan_text("data = pickle.loads(payload)", "handler.py")
        assert any(f.pattern_name == "pickle_deserialize" for f in findings)

    def test_detects_innerhtml(self) -> None:
        findings = scan_text("el.innerHTML = userContent", "app.js")
        assert any(f.pattern_name == "innerHTML_xss" for f in findings)

    def test_detects_sql_fstring(self) -> None:
        findings = scan_text('cursor.execute(f"SELECT * FROM {table}")', "db.py")
        assert any(f.pattern_name == "sql_string_format" for f in findings)

    def test_detects_subprocess_shell(self) -> None:
        findings = scan_text("subprocess.run(cmd, shell=True)", "util.py")
        assert any(f.pattern_name == "subprocess_shell" for f in findings)

    def test_skips_safe_ips(self) -> None:
        findings = scan_text("host = '127.0.0.1'", "config.py")
        ip_findings = [f for f in findings if f.pattern_name == "hardcoded_ip"]
        assert ip_findings == []

    def test_detects_real_ip(self) -> None:
        findings = scan_text("server = '10.45.67.89'", "config.py")
        ip_findings = [f for f in findings if f.pattern_name == "hardcoded_ip"]
        assert len(ip_findings) == 1

    def test_file_extension_filtering(self) -> None:
        # pickle pattern should only match .py files
        findings = scan_text("pickle.loads(x)", "readme.md")
        assert not any(f.pattern_name == "pickle_deserialize" for f in findings)

    def test_sorted_by_severity(self) -> None:
        code = (
            "API_KEY = 'sk-1234567890abcdefghij'\n"  # pragma: allowlist secret
            "eval(x)\n"
            "subprocess.run(cmd, shell=True)\n"
        )
        findings = scan_text(code, "test.py")
        severities = [f.severity for f in findings]
        expected_order = sorted(
            severities,
            key=lambda s: {"critical": 0, "high": 1, "medium": 2, "low": 3}[s],
        )
        assert severities == expected_order

    def test_finding_is_frozen(self) -> None:
        findings = scan_text("eval(x)", "test.py")
        assert len(findings) >= 1
        with __import__("pytest").raises(AttributeError):
            findings[0].message = "changed"  # type: ignore[misc]


class TestScanDiff:
    """Test scanning unified diffs."""

    def test_only_scans_added_lines(self) -> None:
        diff = (
            "+++ b/app.py\n"
            "@@ -1,3 +1,4 @@\n"
            " normal line\n"
            "-removed = eval(old)\n"
            "+added = eval(new)\n"
            " another line\n"
        )
        findings = scan_diff(diff)
        assert len(findings) >= 1
        assert all(f.file_path == "app.py" for f in findings)

    def test_no_findings_on_clean_diff(self) -> None:
        diff = "+++ b/app.py\n@@ -1,2 +1,2 @@\n-old line\n+new clean line\n"
        findings = scan_diff(diff)
        assert findings == []


class TestFormatReport:
    """Test report formatting."""

    def test_no_findings(self) -> None:
        assert "No security issues" in format_scan_report([])

    def test_formats_findings(self) -> None:
        findings = [
            SecurityFinding(
                pattern_name="eval_usage",
                severity=ScanSeverity.HIGH,
                message="eval is dangerous",
                file_path="app.py",
                line_number=10,
                matched_text="eval(",
            ),
        ]
        report = format_scan_report(findings)
        assert "HIGH" in report
        assert "app.py:10" in report
        assert "eval is dangerous" in report
