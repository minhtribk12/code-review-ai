"""Tests for quick git actions from findings."""

from __future__ import annotations

from code_review_agent.interactive.git_actions import (
    generate_fix_commit_message,
    git_status,
)


class TestGitStatus:
    def test_runs_successfully(self) -> None:
        result = git_status()
        assert result.is_success
        assert result.command == "git status --short"


class TestGenerateCommitMessage:
    def test_with_file_and_line(self) -> None:
        msg = generate_fix_commit_message(
            "SQL injection detected",
            "src/db.py",
            42,
            "security",
        )
        assert msg.startswith("fix(security):")
        assert "sql injection" in msg
        assert "db.py:42" in msg

    def test_without_line(self) -> None:
        msg = generate_fix_commit_message("Unused import", "app.py", None, "style")
        assert "fix(style):" in msg
        assert "(app.py)" in msg
        assert ":" not in msg.split("(app.py)")[0].split(":")[-1]  # no line number

    def test_without_file(self) -> None:
        msg = generate_fix_commit_message("General issue", None, None, "performance")
        assert "fix(performance):" in msg
        assert "(" not in msg.split(":")[-1]  # no location

    def test_long_title_truncated(self) -> None:
        long_title = "A" * 100
        msg = generate_fix_commit_message(long_title, "x.py", 1, "sec")
        # Title portion should be at most 50 chars
        title_part = msg.split(":")[1].split("(")[0].strip()
        assert len(title_part) <= 50

    def test_empty_agent(self) -> None:
        msg = generate_fix_commit_message("Bug", "x.py", 1, "")
        assert "fix(review):" in msg
