from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from code_review_agent.config import Settings
from code_review_agent.main import _build_diff_file, _parse_unified_diff, app
from code_review_agent.models import (
    AgentResult,
    DiffStatus,
    ReviewReport,
)
from code_review_agent.token_budget import TokenTier

# ---------------------------------------------------------------------------
# _parse_unified_diff -- status detection
# ---------------------------------------------------------------------------


class TestParseUnifiedDiffStatus:
    """Verify file status detection from git diff headers."""

    def test_modified_file(self) -> None:
        raw = (
            "diff --git a/app.py b/app.py\n"
            "index abc1234..def5678 100644\n"
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -1,3 +1,5 @@\n"
            " existing\n"
            "+new line\n"
        )
        files = _parse_unified_diff(raw_diff=raw)
        assert len(files) == 1
        assert files[0].status == DiffStatus.MODIFIED
        assert files[0].filename == "app.py"

    def test_new_file_via_header(self) -> None:
        raw = (
            "diff --git a/new.py b/new.py\n"
            "new file mode 100644\n"
            "index 0000000..abc1234\n"
            "--- /dev/null\n"
            "+++ b/new.py\n"
            "@@ -0,0 +1,3 @@\n"
            "+def hello():\n"
            "+    pass\n"
        )
        files = _parse_unified_diff(raw_diff=raw)
        assert len(files) == 1
        assert files[0].status == DiffStatus.ADDED
        assert files[0].filename == "new.py"

    def test_new_file_via_dev_null(self) -> None:
        """Detect added file from --- /dev/null without 'new file mode' header."""
        raw = "diff --git a/new.py b/new.py\n--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,2 @@\n+pass\n"
        files = _parse_unified_diff(raw_diff=raw)
        assert files[0].status == DiffStatus.ADDED

    def test_deleted_file_via_header(self) -> None:
        raw = (
            "diff --git a/old.py b/old.py\n"
            "deleted file mode 100644\n"
            "index abc1234..0000000\n"
            "--- a/old.py\n"
            "+++ /dev/null\n"
            "@@ -1,3 +0,0 @@\n"
            "-def old():\n"
            "-    pass\n"
        )
        files = _parse_unified_diff(raw_diff=raw)
        assert len(files) == 1
        assert files[0].status == DiffStatus.DELETED

    def test_deleted_file_via_dev_null(self) -> None:
        """Detect deleted file from +++ /dev/null without 'deleted file mode' header."""
        raw = "diff --git a/old.py b/old.py\n--- a/old.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-pass\n"
        files = _parse_unified_diff(raw_diff=raw)
        assert files[0].status == DiffStatus.DELETED

    def test_renamed_file(self) -> None:
        raw = (
            "diff --git a/old_name.py b/new_name.py\n"
            "similarity index 95%\n"
            "rename from old_name.py\n"
            "rename to new_name.py\n"
            "index abc1234..def5678 100644\n"
            "--- a/old_name.py\n"
            "+++ b/new_name.py\n"
            "@@ -1,3 +1,3 @@\n"
            "-old\n"
            "+new\n"
        )
        files = _parse_unified_diff(raw_diff=raw)
        assert len(files) == 1
        assert files[0].status == DiffStatus.RENAMED
        assert files[0].filename == "new_name.py"


# ---------------------------------------------------------------------------
# _parse_unified_diff -- filename extraction
# ---------------------------------------------------------------------------


class TestParseUnifiedDiffFilename:
    """Verify filename extraction from different diff formats."""

    def test_filename_from_plus_plus_line(self) -> None:
        raw = (
            "diff --git a/src/auth.py b/src/auth.py\n"
            "--- a/src/auth.py\n"
            "+++ b/src/auth.py\n"
            "@@ -1 +1 @@\n"
            "+changed\n"
        )
        files = _parse_unified_diff(raw_diff=raw)
        assert files[0].filename == "src/auth.py"

    def test_filename_from_header_fallback(self) -> None:
        """When +++ line is missing, fall back to diff --git header."""
        raw = "diff --git a/only_header.py b/only_header.py\nBinary files differ\n"
        files = _parse_unified_diff(raw_diff=raw)
        assert files[0].filename == "only_header.py"

    def test_renamed_file_uses_new_name(self) -> None:
        raw = (
            "diff --git a/old.py b/renamed.py\n"
            "rename from old.py\n"
            "rename to renamed.py\n"
            "--- a/old.py\n"
            "+++ b/renamed.py\n"
            "@@ -1 +1 @@\n"
            "+x\n"
        )
        files = _parse_unified_diff(raw_diff=raw)
        assert files[0].filename == "renamed.py"


# ---------------------------------------------------------------------------
# _parse_unified_diff -- multiple files
# ---------------------------------------------------------------------------


class TestParseUnifiedDiffMultipleFiles:
    """Verify parsing diffs with multiple files."""

    def test_two_files(self) -> None:
        raw = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1 +1 @@\n"
            "+a\n"
            "diff --git a/b.py b/b.py\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/b.py\n"
            "@@ -0,0 +1 @@\n"
            "+b\n"
        )
        files = _parse_unified_diff(raw_diff=raw)
        assert len(files) == 2
        assert files[0].filename == "a.py"
        assert files[0].status == DiffStatus.MODIFIED
        assert files[1].filename == "b.py"
        assert files[1].status == DiffStatus.ADDED

    def test_three_files_mixed_status(self) -> None:
        raw = (
            "diff --git a/add.py b/add.py\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/add.py\n"
            "@@ -0,0 +1 @@\n"
            "+new\n"
            "diff --git a/mod.py b/mod.py\n"
            "--- a/mod.py\n"
            "+++ b/mod.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
            "diff --git a/del.py b/del.py\n"
            "deleted file mode 100644\n"
            "--- a/del.py\n"
            "+++ /dev/null\n"
            "@@ -1 +0,0 @@\n"
            "-gone\n"
        )
        files = _parse_unified_diff(raw_diff=raw)
        assert len(files) == 3
        statuses = {f.filename: f.status for f in files}
        assert statuses["add.py"] == DiffStatus.ADDED
        assert statuses["mod.py"] == DiffStatus.MODIFIED
        assert statuses["del.py"] == DiffStatus.DELETED


# ---------------------------------------------------------------------------
# _parse_unified_diff -- edge cases
# ---------------------------------------------------------------------------


class TestParseUnifiedDiffEdgeCases:
    """Verify handling of edge cases and malformed input."""

    def test_empty_diff(self) -> None:
        files = _parse_unified_diff(raw_diff="")
        assert files == []

    def test_no_diff_headers(self) -> None:
        """Plain text without diff --git headers produces no files."""
        files = _parse_unified_diff(raw_diff="just some random text\nno diff here\n")
        assert files == []

    def test_diff_with_no_patch_content(self) -> None:
        raw = "diff --git a/empty.py b/empty.py\n"
        files = _parse_unified_diff(raw_diff=raw)
        assert len(files) == 1
        assert files[0].filename == "empty.py"

    def test_preserves_patch_content(self) -> None:
        raw = (
            "diff --git a/f.py b/f.py\n"
            "--- a/f.py\n"
            "+++ b/f.py\n"
            "@@ -1,3 +1,5 @@\n"
            " context\n"
            "+added\n"
            "-removed\n"
        )
        files = _parse_unified_diff(raw_diff=raw)
        assert "+added\n" in files[0].patch
        assert "-removed\n" in files[0].patch


# ---------------------------------------------------------------------------
# _build_diff_file -- unit tests
# ---------------------------------------------------------------------------


class TestBuildDiffFile:
    """Test the helper that builds DiffFile from accumulated lines."""

    def test_defaults_to_modified(self) -> None:
        lines = [
            "diff --git a/x.py b/x.py\n",
            "--- a/x.py\n",
            "+++ b/x.py\n",
            "@@ -1 +1 @@\n",
        ]
        result = _build_diff_file("x.py", lines)
        assert result.status == DiffStatus.MODIFIED

    def test_header_keyword_takes_priority(self) -> None:
        """'new file mode' header overrides --- /dev/null detection."""
        lines = [
            "diff --git a/x.py b/x.py\n",
            "new file mode 100644\n",
            "--- /dev/null\n",
            "+++ b/x.py\n",
        ]
        result = _build_diff_file("x.py", lines)
        assert result.status == DiffStatus.ADDED


# ---------------------------------------------------------------------------
# _load_settings -- friendly errors
# ---------------------------------------------------------------------------


class TestLoadSettings:
    """Test user-friendly error messages for missing config."""

    def test_missing_api_key_gives_friendly_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.setenv("LLM_PROVIDER", "nvidia")
        # Prevent pydantic-settings from reading .env file
        monkeypatch.chdir(tmp_path)

        from code_review_agent.main import _load_settings

        with pytest.raises(SystemExit, match="An API key is required"):
            _load_settings()

    def test_valid_settings_loads(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("NVIDIA_API_KEY", "sk-test-key-00000000")  # pragma: allowlist secret
        monkeypatch.setenv("LLM_PROVIDER", "nvidia")
        monkeypatch.delenv("LLM_MODEL", raising=False)
        # Prevent pydantic-settings from reading .env file
        monkeypatch.chdir(tmp_path)

        from code_review_agent.main import _load_settings

        settings = _load_settings()
        assert settings.llm_model == "nvidia/nemotron-3-super-120b-a12b"


# ---------------------------------------------------------------------------
# CLI review command -- integration tests (CRA-21)
# ---------------------------------------------------------------------------

runner = CliRunner()


def _make_report() -> ReviewReport:
    """Build a minimal ReviewReport for mocking."""
    return ReviewReport(
        pr_url=None,
        reviewed_at=datetime(2026, 3, 14, 10, 0, 0, tzinfo=UTC),
        agent_results=[
            AgentResult(
                agent_name="security",
                findings=[],
                summary="No issues.",
                execution_time_seconds=0.5,
            ),
        ],
        overall_summary="Clean code.",
        risk_level="low",
    )


class TestReviewCommandValidation:
    """Test input validation on the review command."""

    def test_no_args_exits_with_error(self) -> None:
        result = runner.invoke(app, ["review"])
        assert result.exit_code == 1
        assert "provide either --pr or --diff" in result.output

    def test_both_pr_and_diff_exits_with_error(self, tmp_path: Path) -> None:
        diff_file = tmp_path / "test.patch"
        diff_file.write_text("diff --git a/x.py b/x.py\n")

        result = runner.invoke(app, ["review", "--pr", "owner/repo#1", "--diff", str(diff_file)])
        assert result.exit_code == 1
        assert "only one of" in result.output


class TestReviewCommandWithDiff:
    """Test the review command with --diff input."""

    def test_review_diff_runs_pipeline(self, tmp_path: Path) -> None:
        diff_file = tmp_path / "sample.patch"
        diff_file.write_text(
            "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n+new line\n"
        )

        report = _make_report()

        with (
            patch("code_review_agent.main._load_settings") as mock_settings,
            patch("code_review_agent.main.register_custom_agents"),
            patch("code_review_agent.main.LLMClient"),
            patch("code_review_agent.main.Orchestrator") as mock_orch_cls,
            patch("code_review_agent.main.render_report_rich") as mock_render,
            patch(
                "code_review_agent.main.create_progress_callback",
                return_value=(MagicMock(), None),
            ),
        ):
            settings_obj = MagicMock(spec=Settings)
            settings_obj.github_token = None
            settings_obj.token_tier = TokenTier.FREE
            settings_obj.usage_window = "session"
            settings_obj.history_db_path = "~/.cra/reviews.db"
            mock_settings.return_value = settings_obj
            mock_orch_cls.return_value.run.return_value = report
            mock_render.return_value = None

            result = runner.invoke(app, ["review", "--diff", str(diff_file)])

        assert result.exit_code == 0
        mock_orch_cls.return_value.run.assert_called_once()
        mock_render.assert_called_once()

    def test_review_diff_with_output_saves_file(self, tmp_path: Path) -> None:
        diff_file = tmp_path / "sample.patch"
        diff_file.write_text("diff --git a/x.py b/x.py\n+++ b/x.py\n")
        output_file = tmp_path / "report.md"

        report = _make_report()

        with (
            patch("code_review_agent.main._load_settings") as mock_settings,
            patch("code_review_agent.main.register_custom_agents"),
            patch("code_review_agent.main.LLMClient"),
            patch("code_review_agent.main.Orchestrator") as mock_orch_cls,
            patch("code_review_agent.main.render_report_rich"),
            patch("code_review_agent.main.save_report") as mock_save,
            patch(
                "code_review_agent.main.create_progress_callback",
                return_value=(MagicMock(), None),
            ),
        ):
            settings_obj = MagicMock(spec=Settings)
            settings_obj.github_token = None
            settings_obj.token_tier = TokenTier.FREE
            settings_obj.usage_window = "session"
            settings_obj.history_db_path = "~/.cra/reviews.db"
            mock_settings.return_value = settings_obj
            mock_orch_cls.return_value.run.return_value = report

            result = runner.invoke(
                app, ["review", "--diff", str(diff_file), "--output", str(output_file)]
            )

        assert result.exit_code == 0
        mock_save.assert_called_once()
        assert "Report saved" in result.output

    def test_review_diff_json_format(self, tmp_path: Path) -> None:
        import json

        diff_file = tmp_path / "sample.patch"
        diff_file.write_text(
            "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n+new line\n"
        )

        report = _make_report()

        with (
            patch("code_review_agent.main._load_settings") as mock_settings,
            patch("code_review_agent.main.register_custom_agents"),
            patch("code_review_agent.main.LLMClient"),
            patch("code_review_agent.main.Orchestrator") as mock_orch_cls,
            patch(
                "code_review_agent.main.create_progress_callback",
                return_value=(MagicMock(), None),
            ),
        ):
            settings_obj = MagicMock(spec=Settings)
            settings_obj.github_token = None
            settings_obj.token_tier = TokenTier.FREE
            settings_obj.usage_window = "session"
            settings_obj.history_db_path = "~/.cra/reviews.db"
            mock_settings.return_value = settings_obj
            mock_orch_cls.return_value.run.return_value = report

            result = runner.invoke(app, ["review", "--diff", str(diff_file), "--format", "json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["risk_level"] == "low"
        assert "agent_results" in data

    def test_review_catches_errors_gracefully(self, tmp_path: Path) -> None:
        diff_file = tmp_path / "sample.patch"
        diff_file.write_text("diff --git a/x.py b/x.py\n")

        with patch("code_review_agent.main._load_settings", side_effect=RuntimeError("boom")):
            result = runner.invoke(app, ["review", "--diff", str(diff_file)])

        assert result.exit_code == 1
        assert "boom" in result.output


class TestVersionCommand:
    """Test the --version flag."""

    def test_version_prints_and_exits(self) -> None:
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "code-review-ai" in result.output
        assert "0.1.5" in result.output


# ---------------------------------------------------------------------------
# Settings validation edge cases (CRA-22)
# ---------------------------------------------------------------------------


class TestSettingsValidation:
    """Test Settings validation for edge cases."""

    def test_invalid_provider_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NVIDIA_API_KEY", "sk-test-key-00000000")  # pragma: allowlist secret
        monkeypatch.setenv("LLM_PROVIDER", "invalid_provider")

        with pytest.raises(ValidationError):
            Settings()  # type: ignore[call-arg]

    def test_temperature_too_high_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NVIDIA_API_KEY", "sk-test-key-00000000")  # pragma: allowlist secret
        monkeypatch.setenv("LLM_PROVIDER", "nvidia")
        monkeypatch.setenv("LLM_TEMPERATURE", "1.5")

        with pytest.raises(ValidationError):
            Settings()  # type: ignore[call-arg]

    def test_temperature_negative_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NVIDIA_API_KEY", "sk-test-key-00000000")  # pragma: allowlist secret
        monkeypatch.setenv("LLM_PROVIDER", "nvidia")
        monkeypatch.setenv("LLM_TEMPERATURE", "-0.1")

        with pytest.raises(ValidationError):
            Settings()  # type: ignore[call-arg]

    def test_timeout_zero_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NVIDIA_API_KEY", "sk-test-key-00000000")  # pragma: allowlist secret
        monkeypatch.setenv("LLM_PROVIDER", "nvidia")
        monkeypatch.setenv("REQUEST_TIMEOUT_SECONDS", "0")

        with pytest.raises(ValidationError):
            Settings()  # type: ignore[call-arg]

    def test_custom_base_url_overrides_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "OPENROUTER_API_KEY",
            "sk-test-key-00000000",  # pragma: allowlist secret
        )
        monkeypatch.setenv("LLM_PROVIDER", "openrouter")
        monkeypatch.setenv("LLM_BASE_URL", "http://localhost:8000/v1")

        settings = Settings()  # type: ignore[call-arg]
        assert settings.resolved_llm_base_url == "http://localhost:8000/v1"

    def test_provider_url_used_when_no_override(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("NVIDIA_API_KEY", "sk-test-key-00000000")  # pragma: allowlist secret
        monkeypatch.setenv("LLM_PROVIDER", "nvidia")
        monkeypatch.delenv("LLM_BASE_URL", raising=False)
        # Prevent pydantic-settings from reading .env file
        monkeypatch.chdir(tmp_path)

        settings = Settings()  # type: ignore[call-arg]
        assert "nvidia" in settings.resolved_llm_base_url

    def test_valid_temperature_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NVIDIA_API_KEY", "sk-test-key-00000000")  # pragma: allowlist secret
        monkeypatch.setenv("LLM_PROVIDER", "nvidia")
        monkeypatch.setenv("LLM_TEMPERATURE", "0.5")

        settings = Settings()  # type: ignore[call-arg]
        assert settings.llm_temperature == 0.5

    def test_api_key_is_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NVIDIA_API_KEY", "sk-secret-key-12345")  # pragma: allowlist secret
        monkeypatch.setenv("LLM_PROVIDER", "nvidia")

        settings = Settings()  # type: ignore[call-arg]
        assert "sk-secret-key-12345" not in str(  # pragma: allowlist secret
            settings.nvidia_api_key
        )
        key_value = settings.resolved_api_key.get_secret_value()
        assert key_value == "sk-secret-key-12345"  # pragma: allowlist secret
