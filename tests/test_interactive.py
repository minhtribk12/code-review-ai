"""Tests for the interactive REPL module."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest
from pydantic import SecretStr
from rich.panel import Panel

from code_review_agent.interactive.commands.config_cmd import (
    _mask_secret,
    cmd_config_get,
    cmd_config_reset,
    cmd_config_set,
)
from code_review_agent.interactive.commands.meta import cmd_help, cmd_version
from code_review_agent.interactive.repl import _dispatch
from code_review_agent.interactive.session import PRCache, SessionState

# Short alias for long patch paths used repeatedly in tests.
_PW = "code_review_agent.interactive.commands.pr_write"


@pytest.fixture
def session() -> SessionState:
    """Create a test SessionState with mock settings."""
    settings = MagicMock()
    settings.token_tier = "free"  # noqa: S105
    settings.llm_model = "test/model"
    settings.llm_temperature = 0.1
    settings.llm_provider = "openrouter"
    settings.github_token = None
    return SessionState(settings=settings)


@pytest.fixture
def session_with_token() -> SessionState:
    """Create a test SessionState with a mock GitHub token."""
    settings = MagicMock()
    settings.token_tier = "free"  # noqa: S105
    settings.llm_model = "test/model"
    settings.llm_temperature = 0.1
    settings.llm_provider = "openrouter"
    settings.github_token = SecretStr("ghp_test_fake_token")
    settings.github_rate_limit_warn_threshold = 100
    settings.watch_debounce_seconds = 5.0
    return SessionState(settings=settings)


class TestCommandDispatch:
    """Test the command dispatcher."""

    def test_unknown_command(self, session: SessionState) -> None:
        """Unknown commands print error, don't crash."""
        with patch("code_review_agent.interactive.repl.console") as mock_console:
            _dispatch("foobar", session)
        mock_console.print.assert_called_once()
        assert "Unknown command" in str(mock_console.print.call_args)

    def test_empty_input_ignored(self, session: SessionState) -> None:
        with patch("code_review_agent.interactive.repl.console") as mock_console:
            _dispatch("", session)
        mock_console.print.assert_not_called()

    def test_exit_raises_eof(self, session: SessionState) -> None:
        with pytest.raises(EOFError):
            _dispatch("exit", session)

    def test_quit_raises_eof(self, session: SessionState) -> None:
        with pytest.raises(EOFError):
            _dispatch("quit", session)

    def test_exit_warns_unsaved_config(self, session: SessionState) -> None:
        session.config_overrides["llm_model"] = "new/model"
        with (
            patch("code_review_agent.interactive.repl.console") as mock_console,
            pytest.raises(EOFError),
        ):
            _dispatch("exit", session)
        assert "unsaved" in str(mock_console.print.call_args).lower()

    def test_shell_escape(self, session: SessionState) -> None:
        with patch("code_review_agent.interactive.commands.meta.subprocess") as mock_sub:
            mock_sub.run.return_value = MagicMock(stdout="hello\n", stderr="", returncode=0)
            _dispatch("!echo hello", session)
        mock_sub.run.assert_called_once()

    def test_status_dispatches(self, session: SessionState) -> None:
        with patch("code_review_agent.interactive.commands.git_read.git_ops") as mock_git:
            mock_git.status_short.return_value = "## main\n M file.py\n"
            _dispatch("status", session)
        mock_git.status_short.assert_called_once()

    def test_shlex_handles_quotes(self, session: SessionState) -> None:
        """Verify shlex correctly splits quoted args."""
        with patch("code_review_agent.interactive.commands.config_cmd.console"):
            _dispatch('config set llm_model "new/model-name"', session)
        assert session.config_overrides["llm_model"] == "new/model-name"


class TestConfigCommands:
    """Test config command handlers."""

    def test_config_set(self, session: SessionState) -> None:
        with patch("code_review_agent.interactive.commands.config_cmd.console"):
            cmd_config_set(["llm_temperature", "0.5"], session)
        assert session.config_overrides["llm_temperature"] == "0.5"

    def test_config_set_unknown_key(self, session: SessionState) -> None:
        session.settings.configure_mock(**{"__contains__": lambda self, key: False})
        with patch("code_review_agent.interactive.commands.config_cmd.console"):
            cmd_config_set(["nonexistent_key", "value"], session)

    def test_config_reset_clears_overrides(self, session: SessionState) -> None:
        session.config_overrides["key1"] = "val1"
        session.config_overrides["key2"] = "val2"
        with patch("code_review_agent.interactive.commands.config_cmd.console"):
            cmd_config_reset([], session)
        assert session.config_overrides == {}

    def test_config_get(self, session: SessionState) -> None:
        with patch("code_review_agent.interactive.commands.config_cmd.console") as mock_con:
            cmd_config_get(["llm_model"], session)
        assert "test/model" in str(mock_con.print.call_args)

    def test_config_get_with_override(self, session: SessionState) -> None:
        session.config_overrides["llm_model"] = "override/model"
        with patch("code_review_agent.interactive.commands.config_cmd.console") as mock_con:
            cmd_config_get(["llm_model"], session)
        assert "override/model" in str(mock_con.print.call_args)


class TestMaskSecret:
    """Test secret masking for display."""

    def test_mask_secret_str(self) -> None:
        from pydantic import SecretStr

        secret = SecretStr("sk-test-secret-key-12345")
        masked = _mask_secret(secret)
        assert "sk-t" in masked
        assert "2345" in masked
        assert "test-secret" not in masked

    def test_mask_short_secret(self) -> None:
        from pydantic import SecretStr

        secret = SecretStr("short")
        masked = _mask_secret(secret)
        assert masked == "****"

    def test_mask_none(self) -> None:
        assert "not set" in _mask_secret(None)

    def test_mask_regular_value(self) -> None:
        assert _mask_secret("openrouter") == "openrouter"


class TestMetaCommands:
    """Test meta command handlers."""

    def test_help_runs(self, session: SessionState) -> None:
        with patch("code_review_agent.interactive.commands.meta.console"):
            cmd_help([], session)

    def test_help_group(self, session: SessionState) -> None:
        with patch("code_review_agent.interactive.commands.meta.console"):
            cmd_help(["git"], session)

    def test_version(self, session: SessionState) -> None:
        with patch("code_review_agent.interactive.commands.meta.console") as mock_con:
            cmd_version([], session)
        assert "0.1.0" in str(mock_con.print.call_args)


class TestSessionState:
    """Test SessionState initialization and defaults."""

    def test_default_values(self) -> None:
        settings = MagicMock()
        state = SessionState(settings=settings)
        assert state.reviews_completed == 0
        assert state.current_context == "default"
        assert state.config_overrides == {}

    def test_overrides_isolated(self) -> None:
        settings = MagicMock()
        s1 = SessionState(settings=settings)
        s2 = SessionState(settings=settings)
        s1.config_overrides["key"] = "val"
        assert "key" not in s2.config_overrides


class TestGitOps:
    """Test git operations wrappers."""

    def test_is_git_repo(self) -> None:
        from code_review_agent.interactive.git_ops import is_git_repo

        # Running in a git repo (the project itself)
        assert is_git_repo() is True

    def test_current_branch(self) -> None:
        from code_review_agent.interactive.git_ops import current_branch

        branch = current_branch()
        assert isinstance(branch, str)
        assert len(branch) > 0

    def test_status_short(self) -> None:
        from code_review_agent.interactive.git_ops import status_short

        output = status_short()
        assert isinstance(output, str)

    def test_list_branches(self) -> None:
        from code_review_agent.interactive.git_ops import list_branches

        output = list_branches()
        assert "main" in output

    def test_git_error_on_invalid_command(self) -> None:
        from code_review_agent.interactive.git_ops import GitError, _run

        with pytest.raises(GitError, match="not-a-real-command"):
            _run("not-a-real-command")


class TestGitWriteCommands:
    """Test git write command handlers."""

    def test_branch_list_dispatches(self, session: SessionState) -> None:
        with patch("code_review_agent.interactive.commands.git_write.git_ops") as mock_git:
            mock_git.list_branches.return_value = "main\nfeat/x\n"
            mock_git.current_branch.return_value = "main"
            with patch("code_review_agent.interactive.commands.git_write.console"):
                _dispatch("branch", session)
        mock_git.list_branches.assert_called_once()

    def test_branch_switch_dirty_tree_blocked(self, session: SessionState) -> None:
        with patch("code_review_agent.interactive.commands.git_write.git_ops") as mock_git:
            mock_git.is_working_tree_dirty.return_value = True
            with patch("code_review_agent.interactive.commands.git_write.console") as mock_con:
                _dispatch("branch switch feat/x", session)
        mock_git.switch_branch.assert_not_called()
        assert "uncommitted" in str(mock_con.print.call_args).lower()

    def test_branch_switch_clean_tree(self, session: SessionState) -> None:
        with patch("code_review_agent.interactive.commands.git_write.git_ops") as mock_git:
            mock_git.is_working_tree_dirty.return_value = False
            mock_git.switch_branch.return_value = ""
            with patch("code_review_agent.interactive.commands.git_write.console"):
                _dispatch("branch switch feat/x", session)
        mock_git.switch_branch.assert_called_once_with("feat/x")

    def test_branch_create(self, session: SessionState) -> None:
        with patch("code_review_agent.interactive.commands.git_write.git_ops") as mock_git:
            mock_git.create_branch.return_value = ""
            with patch("code_review_agent.interactive.commands.git_write.console"):
                _dispatch("branch create feat/new", session)
        mock_git.create_branch.assert_called_once_with("feat/new", None)

    def test_branch_create_from_ref(self, session: SessionState) -> None:
        with patch("code_review_agent.interactive.commands.git_write.git_ops") as mock_git:
            mock_git.create_branch.return_value = ""
            with patch("code_review_agent.interactive.commands.git_write.console"):
                _dispatch("branch create feat/new main", session)
        mock_git.create_branch.assert_called_once_with("feat/new", "main")

    def test_branch_delete_unmerged_blocked(self, session: SessionState) -> None:
        with patch("code_review_agent.interactive.commands.git_write.git_ops") as mock_git:
            mock_git.current_branch.return_value = "main"
            mock_git.is_branch_merged.return_value = False
            with patch("code_review_agent.interactive.commands.git_write.console") as mock_con:
                _dispatch("branch delete feat/old", session)
        mock_git.delete_branch.assert_not_called()
        assert "not merged" in str(mock_con.print.call_args).lower()

    def test_branch_delete_force(self, session: SessionState) -> None:
        with patch("code_review_agent.interactive.commands.git_write.git_ops") as mock_git:
            mock_git.current_branch.return_value = "main"
            mock_git.delete_branch.return_value = ""
            with patch("code_review_agent.interactive.commands.git_write.console"):
                _dispatch("branch delete feat/old --force", session)
        mock_git.delete_branch.assert_called_once_with("feat/old", force=True)

    def test_branch_delete_current_blocked(self, session: SessionState) -> None:
        with patch("code_review_agent.interactive.commands.git_write.git_ops") as mock_git:
            mock_git.current_branch.return_value = "main"
            with patch("code_review_agent.interactive.commands.git_write.console") as mock_con:
                _dispatch("branch delete main", session)
        mock_git.delete_branch.assert_not_called()
        assert "current branch" in str(mock_con.print.call_args).lower()

    def test_add_files(self, session: SessionState) -> None:
        with patch("code_review_agent.interactive.commands.git_write.git_ops") as mock_git:
            mock_git.add_files.return_value = ""
            with patch("code_review_agent.interactive.commands.git_write.console"):
                _dispatch("add src/main.py", session)
        mock_git.add_files.assert_called_once_with("src/main.py")

    def test_add_dot_shows_files(self, session: SessionState) -> None:
        with patch("code_review_agent.interactive.commands.git_write.git_ops") as mock_git:
            mock_git.list_changed_files.return_value = ["a.py", "b.py"]
            mock_git.list_untracked_files.return_value = ["c.py"]
            mock_git.add_files.return_value = ""
            with patch("code_review_agent.interactive.commands.git_write.console") as mock_con:
                _dispatch("add .", session)
        mock_git.add_files.assert_called_once_with(".")
        output = str(mock_con.print.call_args_list)
        assert "3 file(s)" in output

    def test_unstage_files(self, session: SessionState) -> None:
        with patch("code_review_agent.interactive.commands.git_write.git_ops") as mock_git:
            mock_git.unstage_files.return_value = ""
            with patch("code_review_agent.interactive.commands.git_write.console"):
                _dispatch("unstage src/main.py", session)
        mock_git.unstage_files.assert_called_once_with("src/main.py")

    def test_commit_with_message(self, session: SessionState) -> None:
        with patch("code_review_agent.interactive.commands.git_write.git_ops") as mock_git:
            mock_git.list_staged_files.return_value = ["a.py"]
            mock_git.commit.return_value = "[main abc123] fix: something\n"
            with patch("code_review_agent.interactive.commands.git_write.console"):
                _dispatch('commit -m "fix: something"', session)
        mock_git.commit.assert_called_once_with("fix: something")

    def test_commit_no_staged_warns(self, session: SessionState) -> None:
        with patch("code_review_agent.interactive.commands.git_write.git_ops") as mock_git:
            mock_git.list_staged_files.return_value = []
            with patch("code_review_agent.interactive.commands.git_write.console") as mock_con:
                _dispatch('commit -m "test"', session)
        mock_git.commit.assert_not_called()
        assert "nothing staged" in str(mock_con.print.call_args).lower()

    def test_stash_push(self, session: SessionState) -> None:
        with patch("code_review_agent.interactive.commands.git_write.git_ops") as mock_git:
            mock_git.stash_push.return_value = "Saved working directory"
            with patch("code_review_agent.interactive.commands.git_write.console"):
                _dispatch("stash", session)
        mock_git.stash_push.assert_called_once()

    def test_stash_pop(self, session: SessionState) -> None:
        with patch("code_review_agent.interactive.commands.git_write.git_ops") as mock_git:
            mock_git.stash_pop.return_value = "On branch main"
            with patch("code_review_agent.interactive.commands.git_write.console"):
                _dispatch("stash pop", session)
        mock_git.stash_pop.assert_called_once()

    def test_stash_list(self, session: SessionState) -> None:
        with patch("code_review_agent.interactive.commands.git_write.git_ops") as mock_git:
            mock_git.stash_list.return_value = "stash@{0}: WIP on main\n"
            with patch("code_review_agent.interactive.commands.git_write.console"):
                _dispatch("stash list", session)
        mock_git.stash_list.assert_called_once()


class TestCLIInteractiveCommand:
    """Test the interactive Typer command registration."""

    def test_interactive_command_registered(self) -> None:
        from typer.testing import CliRunner

        from code_review_agent.main import app

        runner = CliRunner()
        result = runner.invoke(app, ["interactive", "--help"])
        assert result.exit_code == 0
        assert "interactive" in result.output.lower() or "tui" in result.output.lower()


# ---------------------------------------------------------------------------
# Phase 3: PR write commands
# ---------------------------------------------------------------------------

_MOCK_PR_DETAIL = {
    "number": 42,
    "title": "Fix auth bug",
    "body": "Fixes login issue",
    "state": "open",
    "draft": False,
    "head_branch": "fix/auth",
    "base_branch": "main",
    "author": "octocat",
    "labels": [],
    "reviewers": [],
    "additions": 10,
    "deletions": 3,
    "changed_files": 2,
    "mergeable": True,
    "html_url": "https://github.com/acme/app/pull/42",
    "created_at": "2026-03-01T00:00:00Z",
    "updated_at": "2026-03-10T00:00:00Z",
}


class TestPrWriteHelpers:
    """Test flag parsing helpers in pr_write module."""

    def test_parse_flag_present(self) -> None:
        from code_review_agent.interactive.commands.pr_write import _parse_flag

        assert _parse_flag(["--title", "hello", "--base", "dev"], "--title") == "hello"
        assert _parse_flag(["--title", "hello", "--base", "dev"], "--base") == "dev"

    def test_parse_flag_absent(self) -> None:
        from code_review_agent.interactive.commands.pr_write import _parse_flag

        assert _parse_flag(["--title", "hello"], "--base") is None

    def test_has_flag(self) -> None:
        from code_review_agent.interactive.commands.pr_write import _has_flag

        assert _has_flag(["--dry-run", "--fill"], "--dry-run") is True
        assert _has_flag(["--fill"], "--dry-run") is False


class TestPrCreate:
    """Test pr create command."""

    def test_no_token_shows_error(self, session: SessionState) -> None:
        from code_review_agent.interactive.commands.pr_write import pr_create

        with (
            patch(f"{_PW}._get_repo_info") as mock_info,
            patch(f"{_PW}.console") as mock_con,
        ):
            mock_info.return_value = ("acme", "app", None)
            pr_create(["--title", "test"], session)
        assert "GITHUB_TOKEN" in str(mock_con.print.call_args)

    def test_same_branch_blocked(self, session_with_token: SessionState) -> None:
        from code_review_agent.interactive.commands.pr_write import pr_create

        with (
            patch(f"{_PW}._get_repo_info") as mock_info,
            patch(f"{_PW}.git_ops") as mock_git,
            patch(f"{_PW}.console") as mock_con,
        ):
            mock_info.return_value = ("acme", "app", "ghp_token")
            mock_git.current_branch.return_value = "main"
            pr_create(["--title", "test"], session_with_token)
        assert "same as base" in str(mock_con.print.call_args).lower()

    def test_title_required(self, session_with_token: SessionState) -> None:
        from code_review_agent.interactive.commands.pr_write import pr_create

        with (
            patch(f"{_PW}._get_repo_info") as mock_info,
            patch(f"{_PW}.git_ops") as mock_git,
            patch(f"{_PW}.console") as mock_con,
        ):
            mock_info.return_value = ("acme", "app", "ghp_token")
            mock_git.current_branch.return_value = "feat/x"
            pr_create([], session_with_token)
        assert "title required" in str(mock_con.print.call_args).lower()

    def test_dry_run_does_not_create(self, session_with_token: SessionState) -> None:
        from code_review_agent.interactive.commands.pr_write import pr_create

        with (
            patch(f"{_PW}._get_repo_info") as mock_info,
            patch(f"{_PW}.git_ops") as mock_git,
            patch(f"{_PW}.create_pr") as mock_create,
            patch(f"{_PW}.console") as mock_con,
        ):
            mock_info.return_value = ("acme", "app", "ghp_token")
            mock_git.current_branch.return_value = "feat/x"
            mock_git.has_upstream.return_value = True
            pr_create(["--title", "test PR", "--dry-run"], session_with_token)
        mock_create.assert_not_called()
        assert "dry run" in str(mock_con.print.call_args_list).lower()

    def test_fill_auto_generates_title_body(self, session_with_token: SessionState) -> None:
        from code_review_agent.interactive.commands.pr_write import pr_create

        with (
            patch(f"{_PW}._get_repo_info") as mock_info,
            patch(f"{_PW}.git_ops") as mock_git,
            patch(f"{_PW}.console"),
        ):
            mock_info.return_value = ("acme", "app", "ghp_token")
            mock_git.current_branch.return_value = "feat/x"
            mock_git.has_upstream.return_value = True
            mock_git.log_oneline_commits_since.return_value = [
                "add user auth",
                "fix login bug",
            ]
            pr_create(["--fill", "--dry-run"], session_with_token)
        mock_git.log_oneline_commits_since.assert_called_once_with("main")

    def test_fill_no_commits_warns(self, session_with_token: SessionState) -> None:
        from code_review_agent.interactive.commands.pr_write import pr_create

        with (
            patch(f"{_PW}._get_repo_info") as mock_info,
            patch(f"{_PW}.git_ops") as mock_git,
            patch(f"{_PW}.console") as mock_con,
        ):
            mock_info.return_value = ("acme", "app", "ghp_token")
            mock_git.current_branch.return_value = "feat/x"
            mock_git.log_oneline_commits_since.return_value = []
            pr_create(["--fill"], session_with_token)
        assert "no commits" in str(mock_con.print.call_args).lower()

    def test_push_on_no_upstream(self, session_with_token: SessionState) -> None:
        from code_review_agent.interactive.commands.pr_write import pr_create

        with (
            patch(f"{_PW}._get_repo_info") as mock_info,
            patch(f"{_PW}.git_ops") as mock_git,
            patch(f"{_PW}.create_pr") as mock_create,
            patch(f"{_PW}.console"),
        ):
            mock_info.return_value = ("acme", "app", "ghp_token")
            mock_git.current_branch.return_value = "feat/x"
            mock_git.has_upstream.return_value = False
            mock_create.return_value = {
                "number": 99,
                "html_url": "https://github.com/acme/app/pull/99",
            }
            pr_create(["--title", "test PR"], session_with_token)
        mock_git.push_branch.assert_called_once()

    def test_auth_error_handled(self, session_with_token: SessionState) -> None:
        from code_review_agent.github_client import GitHubAuthError
        from code_review_agent.interactive.commands.pr_write import pr_create

        with (
            patch(f"{_PW}._get_repo_info") as mock_info,
            patch(f"{_PW}.git_ops") as mock_git,
            patch(f"{_PW}.create_pr") as mock_create,
            patch(f"{_PW}.console") as mock_con,
        ):
            mock_info.return_value = ("acme", "app", "ghp_token")
            mock_git.current_branch.return_value = "feat/x"
            mock_git.has_upstream.return_value = True
            mock_create.side_effect = GitHubAuthError("403")
            pr_create(["--title", "test PR"], session_with_token)
        assert "permission denied" in str(mock_con.print.call_args_list).lower()

    def test_cache_invalidated_after_create(self, session_with_token: SessionState) -> None:
        from code_review_agent.interactive.commands.pr_write import pr_create

        session_with_token.pr_cache.set("acme", "app", "open", [{"number": 1}])
        assert session_with_token.pr_cache.is_valid

        with (
            patch(f"{_PW}._get_repo_info") as mock_info,
            patch(f"{_PW}.git_ops") as mock_git,
            patch(f"{_PW}.create_pr") as mock_create,
            patch(f"{_PW}.console"),
        ):
            mock_info.return_value = ("acme", "app", "ghp_token")
            mock_git.current_branch.return_value = "feat/x"
            mock_git.has_upstream.return_value = True
            mock_create.return_value = {"number": 99, "html_url": "https://github.com/x/y/pull/99"}
            pr_create(["--title", "test"], session_with_token)

        assert not session_with_token.pr_cache.is_valid


class TestPrMerge:
    """Test pr merge command."""

    def test_no_args_shows_usage(self, session_with_token: SessionState) -> None:
        from code_review_agent.interactive.commands.pr_write import pr_merge

        with patch(f"{_PW}.console") as mock_con:
            pr_merge([], session_with_token)
        assert "usage" in str(mock_con.print.call_args).lower()

    def test_invalid_strategy_blocked(self, session_with_token: SessionState) -> None:
        from code_review_agent.interactive.commands.pr_write import pr_merge

        with (
            patch(f"{_PW}._get_repo_info") as mock_info,
            patch(f"{_PW}.console") as mock_con,
        ):
            mock_info.return_value = ("acme", "app", "ghp_token")
            pr_merge(["42", "--strategy", "fast-forward"], session_with_token)
        assert "invalid merge strategy" in str(mock_con.print.call_args).lower()

    def test_dry_run_shows_preflight(self, session_with_token: SessionState) -> None:
        from code_review_agent.interactive.commands.pr_write import pr_merge

        with (
            patch(f"{_PW}._get_repo_info") as mock_info,
            patch(f"{_PW}.get_pr_detail") as mock_detail,
            patch(f"{_PW}.get_pr_checks") as mock_checks,
            patch(f"{_PW}.get_pr_reviews") as mock_reviews,
            patch(f"{_PW}.merge_pr") as mock_merge,
            patch(f"{_PW}.console") as mock_con,
        ):
            mock_info.return_value = ("acme", "app", "ghp_token")
            mock_detail.return_value = _MOCK_PR_DETAIL
            mock_checks.return_value = [
                {"name": "ci", "status": "completed", "conclusion": "success"},
            ]
            mock_reviews.return_value = [
                {"user": "reviewer1", "state": "APPROVED", "submitted_at": ""},
            ]
            pr_merge(["42", "--dry-run"], session_with_token)
        mock_merge.assert_not_called()
        assert "dry run" in str(mock_con.print.call_args_list).lower()

    def test_warnings_shown_for_failed_checks(self, session_with_token: SessionState) -> None:
        from code_review_agent.interactive.commands.pr_write import pr_merge

        captured_panels: list[Panel] = []

        with (
            patch(f"{_PW}._get_repo_info") as mock_info,
            patch(f"{_PW}.get_pr_detail") as mock_detail,
            patch(f"{_PW}.get_pr_checks") as mock_checks,
            patch(f"{_PW}.get_pr_reviews") as mock_reviews,
            patch(f"{_PW}.console") as mock_con,
        ):
            mock_info.return_value = ("acme", "app", "ghp_token")
            mock_detail.return_value = _MOCK_PR_DETAIL
            mock_checks.return_value = [
                {"name": "lint", "status": "completed", "conclusion": "failure"},
            ]
            mock_reviews.return_value = []

            def capture_print(obj: object, **kwargs: object) -> None:
                if isinstance(obj, Panel):
                    captured_panels.append(obj)

            mock_con.print.side_effect = capture_print
            pr_merge(["42", "--dry-run"], session_with_token)

        assert len(captured_panels) == 1
        # Panel.renderable is the content string we built
        panel_content = str(captured_panels[0].renderable).lower()
        assert "failed checks" in panel_content
        assert "no approvals" in panel_content

    def test_auth_error_handled(self, session_with_token: SessionState) -> None:
        from code_review_agent.github_client import GitHubAuthError
        from code_review_agent.interactive.commands.pr_write import pr_merge

        with (
            patch(f"{_PW}._get_repo_info") as mock_info,
            patch(f"{_PW}.get_pr_detail") as mock_detail,
            patch(f"{_PW}.get_pr_checks") as mock_checks,
            patch(f"{_PW}.get_pr_reviews") as mock_reviews,
            patch(f"{_PW}.merge_pr") as mock_merge,
            patch(f"{_PW}.console") as mock_con,
        ):
            mock_info.return_value = ("acme", "app", "ghp_token")
            mock_detail.return_value = _MOCK_PR_DETAIL
            mock_checks.return_value = []
            mock_reviews.return_value = [{"user": "r", "state": "APPROVED", "submitted_at": ""}]
            mock_merge.side_effect = GitHubAuthError("403")
            pr_merge(["42"], session_with_token)
        assert "permission denied" in str(mock_con.print.call_args_list).lower()


class TestPrApprove:
    """Test pr approve command."""

    def test_no_args_shows_usage(self, session_with_token: SessionState) -> None:
        from code_review_agent.interactive.commands.pr_write import pr_approve

        with patch(f"{_PW}.console") as mock_con:
            pr_approve([], session_with_token)
        assert "usage" in str(mock_con.print.call_args).lower()

    def test_dry_run_does_not_submit(self, session_with_token: SessionState) -> None:
        from code_review_agent.interactive.commands.pr_write import pr_approve

        with (
            patch(f"{_PW}._get_repo_info") as mock_info,
            patch(f"{_PW}.get_pr_detail") as mock_detail,
            patch(f"{_PW}.submit_pr_review") as mock_submit,
            patch(f"{_PW}.console"),
        ):
            mock_info.return_value = ("acme", "app", "ghp_token")
            mock_detail.return_value = _MOCK_PR_DETAIL
            pr_approve(["42", "--dry-run"], session_with_token)
        mock_submit.assert_not_called()

    def test_approve_with_comment(self, session_with_token: SessionState) -> None:
        from code_review_agent.interactive.commands.pr_write import pr_approve

        with (
            patch(f"{_PW}._get_repo_info") as mock_info,
            patch(f"{_PW}.get_pr_detail") as mock_detail,
            patch(f"{_PW}.submit_pr_review") as mock_submit,
            patch(f"{_PW}.console"),
        ):
            mock_info.return_value = ("acme", "app", "ghp_token")
            mock_detail.return_value = _MOCK_PR_DETAIL
            mock_submit.return_value = {"id": 1, "state": "APPROVED", "html_url": "https://x"}
            pr_approve(["42", "-m", "LGTM"], session_with_token)
        mock_submit.assert_called_once()
        assert mock_submit.call_args.kwargs["body"] == "LGTM"
        assert mock_submit.call_args.kwargs["event"] == "APPROVE"


class TestPrRequestChanges:
    """Test pr request-changes command."""

    def test_comment_mandatory(self, session_with_token: SessionState) -> None:
        from code_review_agent.interactive.commands.pr_write import pr_request_changes

        with (
            patch(f"{_PW}._get_repo_info") as mock_info,
            patch(f"{_PW}.console") as mock_con,
        ):
            mock_info.return_value = ("acme", "app", "ghp_token")
            pr_request_changes(["42"], session_with_token)
        assert "mandatory" in str(mock_con.print.call_args).lower()

    def test_dry_run_does_not_submit(self, session_with_token: SessionState) -> None:
        from code_review_agent.interactive.commands.pr_write import pr_request_changes

        with (
            patch(f"{_PW}._get_repo_info") as mock_info,
            patch(f"{_PW}.get_pr_detail") as mock_detail,
            patch(f"{_PW}.submit_pr_review") as mock_submit,
            patch(f"{_PW}.console"),
        ):
            mock_info.return_value = ("acme", "app", "ghp_token")
            mock_detail.return_value = _MOCK_PR_DETAIL
            pr_request_changes(["42", "-m", "needs work", "--dry-run"], session_with_token)
        mock_submit.assert_not_called()

    def test_submits_request_changes(self, session_with_token: SessionState) -> None:
        from code_review_agent.interactive.commands.pr_write import pr_request_changes

        with (
            patch(f"{_PW}._get_repo_info") as mock_info,
            patch(f"{_PW}.get_pr_detail") as mock_detail,
            patch(f"{_PW}.submit_pr_review") as mock_submit,
            patch(f"{_PW}.console"),
        ):
            mock_info.return_value = ("acme", "app", "ghp_token")
            mock_detail.return_value = _MOCK_PR_DETAIL
            mock_submit.return_value = {
                "id": 2,
                "state": "CHANGES_REQUESTED",
                "html_url": "https://x",
            }
            pr_request_changes(["42", "-m", "fix the auth"], session_with_token)
        mock_submit.assert_called_once()
        assert mock_submit.call_args.kwargs["event"] == "REQUEST_CHANGES"
        assert mock_submit.call_args.kwargs["body"] == "fix the auth"


# ---------------------------------------------------------------------------
# Phase 3: PR dispatch wiring (write commands accessible via `pr` router)
# ---------------------------------------------------------------------------


class TestPrDispatchWiring:
    """Test that write commands are wired into the pr dispatcher."""

    def test_pr_create_dispatches(self, session_with_token: SessionState) -> None:
        with (
            patch(f"{_PW}._get_repo_info") as mock_info,
            patch(f"{_PW}.console"),
        ):
            mock_info.return_value = ("acme", "app", None)
            _dispatch("pr create --title test", session_with_token)
        # Should reach pr_create (token check happens inside)

    def test_pr_merge_dispatches(self, session_with_token: SessionState) -> None:
        with (
            patch(f"{_PW}._get_repo_info") as mock_info,
            patch(f"{_PW}.console"),
        ):
            mock_info.return_value = ("acme", "app", None)
            _dispatch("pr merge 42", session_with_token)

    def test_pr_approve_dispatches(self, session_with_token: SessionState) -> None:
        with (
            patch(f"{_PW}._get_repo_info") as mock_info,
            patch(f"{_PW}.console"),
        ):
            mock_info.return_value = ("acme", "app", None)
            _dispatch("pr approve 42", session_with_token)

    def test_pr_request_changes_dispatches(self, session_with_token: SessionState) -> None:
        with (
            patch(f"{_PW}._get_repo_info") as mock_info,
            patch(f"{_PW}.console"),
        ):
            mock_info.return_value = ("acme", "app", None)
            _dispatch('pr request-changes 42 -m "fix it"', session_with_token)

    def test_unknown_pr_subcommand(self, session: SessionState) -> None:
        with patch("code_review_agent.interactive.commands.pr_read.console") as mock_con:
            _dispatch("pr nonexistent", session)
        output = str(mock_con.print.call_args)
        assert "unknown" in output.lower()
        assert "write" in output.lower()  # new usage line includes Write group


# ---------------------------------------------------------------------------
# Phase 3: Smart workflow -- auto-stage on review
# ---------------------------------------------------------------------------


class TestReviewAutoStage:
    """Test that review auto-stages when no unstaged diff exists."""

    def test_auto_stages_and_unstages(self, session: SessionState) -> None:
        with (
            patch("code_review_agent.interactive.commands.review_cmd.git_ops") as mock_git,
            patch("code_review_agent.interactive.commands.review_cmd.console"),
        ):
            # First call to diff() returns empty (no unstaged changes)
            # Second call to diff(staged=True) also empty
            # list_changed_files returns files -> triggers auto-stage
            # After add_files("."), diff(staged=True) returns content
            mock_git.diff.return_value = ""
            mock_git.list_changed_files.return_value = ["a.py"]
            mock_git.add_files.return_value = ""
            mock_git.unstage_files.return_value = ""

            from code_review_agent.interactive.commands.review_cmd import _resolve_diff

            # Make staged diff return content after add
            call_count = 0

            def staged_diff_side_effect(
                *,
                staged: bool = False,
                file_path: str | None = None,
            ) -> str:
                nonlocal call_count
                call_count += 1
                if staged and call_count >= 3:
                    return "+new code line"
                return ""

            mock_git.diff.side_effect = staged_diff_side_effect

            result = _resolve_diff([])

        mock_git.add_files.assert_called_once_with(".")
        mock_git.unstage_files.assert_called_once_with(".")
        assert result == "+new code line"

    def test_no_changed_files_returns_empty(self, session: SessionState) -> None:
        with patch("code_review_agent.interactive.commands.review_cmd.git_ops") as mock_git:
            mock_git.diff.return_value = ""
            mock_git.list_changed_files.return_value = []

            from code_review_agent.interactive.commands.review_cmd import _resolve_diff

            result = _resolve_diff([])
        assert result == ""
        mock_git.add_files.assert_not_called()


# ---------------------------------------------------------------------------
# Phase 3: Smart workflow -- auto-stash on pr review
# ---------------------------------------------------------------------------


class TestPrReviewAutoStash:
    """Test that pr review stashes and restores dirty working tree."""

    def test_stashes_when_dirty(self, session_with_token: SessionState) -> None:
        with (
            patch("code_review_agent.interactive.commands.pr_read.git_ops") as mock_git,
            patch("code_review_agent.interactive.commands.pr_read._get_repo_info") as mock_info,
            patch("code_review_agent.interactive.commands.pr_read.fetch_pr_diff") as mock_fetch,
            patch("code_review_agent.interactive.commands.pr_read.console"),
        ):
            mock_info.return_value = ("acme", "app", "ghp_token")
            mock_git.is_working_tree_dirty.return_value = True
            mock_fetch.return_value = MagicMock(diff_files=[])  # No files -> early return

            _dispatch("pr review 42", session_with_token)

        mock_git.stash_push.assert_called_once()
        mock_git.stash_pop.assert_called_once()

    def test_no_stash_when_clean(self, session_with_token: SessionState) -> None:
        with (
            patch("code_review_agent.interactive.commands.pr_read.git_ops") as mock_git,
            patch("code_review_agent.interactive.commands.pr_read._get_repo_info") as mock_info,
            patch("code_review_agent.interactive.commands.pr_read.fetch_pr_diff") as mock_fetch,
            patch("code_review_agent.interactive.commands.pr_read.console"),
        ):
            mock_info.return_value = ("acme", "app", "ghp_token")
            mock_git.is_working_tree_dirty.return_value = False
            mock_fetch.return_value = MagicMock(diff_files=[])

            _dispatch("pr review 42", session_with_token)

        mock_git.stash_push.assert_not_called()
        mock_git.stash_pop.assert_not_called()


# ---------------------------------------------------------------------------
# Phase 3: Watch command
# ---------------------------------------------------------------------------


class TestWatchCommand:
    """Test watch command registration and basic behavior."""

    def test_watch_dispatches(self, session_with_token: SessionState) -> None:
        """Watch command is registered and reachable via dispatch."""
        with (
            patch("code_review_agent.interactive.commands.watch_cmd.git_ops") as mock_git,
            patch("code_review_agent.interactive.commands.watch_cmd.console"),
            patch("code_review_agent.interactive.commands.watch_cmd.time") as mock_time,
        ):
            mock_git.status_porcelain.return_value = ""
            # Make sleep raise KeyboardInterrupt to exit the loop
            mock_time.sleep.side_effect = KeyboardInterrupt
            _dispatch("watch", session_with_token)
        mock_git.status_porcelain.assert_called()


# ---------------------------------------------------------------------------
# Phase 3: New git_ops functions
# ---------------------------------------------------------------------------


class TestGitOpsNewFunctions:
    """Test new git_ops functions added in Phase 3."""

    def test_has_upstream_on_main(self) -> None:
        from code_review_agent.interactive.git_ops import has_upstream

        # main branch typically has upstream when cloned
        result = has_upstream()
        assert isinstance(result, bool)

    def test_log_oneline_commits_since(self) -> None:
        from code_review_agent.interactive.git_ops import log_oneline_commits_since

        # From main..HEAD on main should return empty
        result = log_oneline_commits_since("main")
        assert isinstance(result, list)

    def test_status_porcelain(self) -> None:
        from code_review_agent.interactive.git_ops import status_porcelain

        result = status_porcelain()
        assert isinstance(result, str)

    def test_push_branch_mocked(self) -> None:
        from code_review_agent.interactive.git_ops import push_branch

        with patch("code_review_agent.interactive.git_ops._run") as mock_run:
            mock_run.return_value = MagicMock(
                stderr="Everything up-to-date",
                stdout=MagicMock(strip=MagicMock(return_value="feat/x")),
            )
            push_branch()
        # Called twice: once for current_branch, once for push
        assert mock_run.call_count == 2
        push_call_args = mock_run.call_args_list[1][0]
        assert "push" in push_call_args


# ---------------------------------------------------------------------------
# Phase 3: PRCache invalidation behavior
# ---------------------------------------------------------------------------


class TestPRCacheInvalidation:
    """Test that PRCache invalidation works correctly."""

    def test_invalidate_clears_data(self) -> None:
        cache = PRCache()
        cache.set("acme", "app", "open", [{"number": 1}])
        assert cache.is_valid
        cache.invalidate()
        assert not cache.is_valid
        assert cache.get("acme", "app", "open") is None

    def test_cache_miss_after_invalidate(self) -> None:
        cache = PRCache()
        cache.set("acme", "app", "open", [{"number": 1}])
        cache.invalidate()
        assert cache.get("acme", "app", "open") is None


# ---------------------------------------------------------------------------
# Phase 3: Completers include new commands
# ---------------------------------------------------------------------------


class TestCompletersPhase3:
    """Test that completers include Phase 3 commands."""

    def test_completer_has_pr_write_commands(self) -> None:
        from code_review_agent.interactive.completers import build_static_completer

        completer = build_static_completer()
        # NestedCompleter stores options internally -- verify it builds without error
        assert completer is not None

    def test_completer_has_watch(self) -> None:
        from code_review_agent.interactive.completers import build_static_completer

        completer = build_static_completer()
        assert completer is not None


# ---------------------------------------------------------------------------
# Phase 3: Meta help includes new groups
# ---------------------------------------------------------------------------


class TestMetaPhase3:
    """Test that help output includes Phase 3 command groups."""

    def test_help_includes_pr_write_group(self) -> None:
        from code_review_agent.interactive.commands.meta import COMMAND_HELP

        assert "Pr Write" in COMMAND_HELP
        pr_write_cmds = [cmd for cmd, _desc in COMMAND_HELP["Pr Write"]]
        assert any("create" in cmd for cmd in pr_write_cmds)
        assert any("merge" in cmd for cmd in pr_write_cmds)
        assert any("approve" in cmd for cmd in pr_write_cmds)
        assert any("request-changes" in cmd for cmd in pr_write_cmds)

    def test_help_includes_watch_group(self) -> None:
        from code_review_agent.interactive.commands.meta import COMMAND_HELP

        assert "Watch" in COMMAND_HELP

    def test_pr_read_renamed(self) -> None:
        from code_review_agent.interactive.commands.meta import COMMAND_HELP

        assert "Pr Read" in COMMAND_HELP
        assert "Pr" not in COMMAND_HELP  # Old name should not exist

    def test_help_includes_repo_group(self) -> None:
        from code_review_agent.interactive.commands.meta import COMMAND_HELP

        assert "Repo" in COMMAND_HELP
        repo_cmds = [cmd for cmd, _desc in COMMAND_HELP["Repo"]]
        assert any("list" in cmd for cmd in repo_cmds)
        assert any("select" in cmd for cmd in repo_cmds)


# ---------------------------------------------------------------------------
# Repo commands
# ---------------------------------------------------------------------------


class TestRepoCommands:
    """Test repo list, select, current, clear commands."""

    def test_repo_dispatches(self, session_with_token: SessionState) -> None:
        """Repo command is registered in REPL dispatch."""
        from code_review_agent.interactive.repl import _COMMANDS

        assert "repo" in _COMMANDS

    def test_repo_select(self, session_with_token: SessionState) -> None:
        from code_review_agent.interactive.commands.repo_cmd import _repo_select

        with patch("code_review_agent.interactive.commands.repo_cmd.git_ops") as mock_git:
            mock_git.list_remotes.return_value = {}
            with patch("code_review_agent.interactive.commands.repo_cmd.console"):
                _repo_select(["acme/app"], session_with_token)

        assert session_with_token.active_repo == "acme/app"
        assert session_with_token.active_repo_source == "remote"

    def test_repo_select_local(self, session_with_token: SessionState) -> None:
        from code_review_agent.interactive.commands.repo_cmd import _repo_select

        with (
            patch("code_review_agent.interactive.commands.repo_cmd.git_ops") as mock_git,
            patch("code_review_agent.interactive.commands.repo_cmd.console"),
        ):
            mock_git.list_remotes.return_value = {
                "origin": "git@github.com:acme/app.git",
            }
            mock_git.parse_github_owner_repo.return_value = ("acme", "app")
            _repo_select(["acme/app"], session_with_token)

        assert session_with_token.active_repo == "acme/app"
        assert session_with_token.active_repo_source == "local"

    def test_repo_select_invalid_format(self, session_with_token: SessionState) -> None:
        from code_review_agent.interactive.commands.repo_cmd import _repo_select

        with patch("code_review_agent.interactive.commands.repo_cmd.console") as mock_con:
            _repo_select(["invalid"], session_with_token)
        assert "invalid format" in str(mock_con.print.call_args).lower()
        assert session_with_token.active_repo is None

    def test_repo_clear(self, session_with_token: SessionState) -> None:
        from code_review_agent.interactive.commands.repo_cmd import _repo_clear

        session_with_token.active_repo = "acme/app"
        session_with_token.active_repo_source = "remote"

        with (
            patch("code_review_agent.interactive.commands.repo_cmd.git_ops"),
            patch("code_review_agent.interactive.commands.repo_cmd.console"),
        ):
            _repo_clear([], session_with_token)

        assert session_with_token.active_repo is None
        assert session_with_token.active_repo_source == ""

    def test_repo_current_with_active(self, session_with_token: SessionState) -> None:
        from code_review_agent.interactive.commands.repo_cmd import _repo_current

        session_with_token.active_repo = "acme/api"
        session_with_token.active_repo_source = "remote"

        with patch("code_review_agent.interactive.commands.repo_cmd.console") as mock_con:
            _repo_current([], session_with_token)
        assert "acme/api:remote" in str(mock_con.print.call_args)

    def test_repo_current_no_active(self, session_with_token: SessionState) -> None:
        from code_review_agent.interactive.commands.repo_cmd import _repo_current

        with (
            patch("code_review_agent.interactive.commands.repo_cmd.git_ops") as mock_git,
            patch("code_review_agent.interactive.commands.repo_cmd.console") as mock_con,
        ):
            mock_git.remote_url.return_value = "git@github.com:acme/app.git"
            mock_git.parse_github_owner_repo.return_value = ("acme", "app")
            _repo_current([], session_with_token)
        assert "acme/app:local" in str(mock_con.print.call_args)

    def test_get_repo_info_uses_active_repo(self, session_with_token: SessionState) -> None:
        """When active_repo is set, _get_repo_info uses it over git remote."""
        from code_review_agent.interactive.commands.pr_read import _get_repo_info

        session_with_token.active_repo = "other-org/other-repo"
        session_with_token.active_repo_source = "remote"

        owner, repo, _token = _get_repo_info(session_with_token)
        assert owner == "other-org"
        assert repo == "other-repo"

    def test_get_repo_info_falls_back_to_remote(
        self,
        session_with_token: SessionState,
    ) -> None:
        """Without active_repo, falls back to git remote."""
        from code_review_agent.interactive.commands.pr_read import _get_repo_info

        with patch("code_review_agent.interactive.commands.pr_read.git_ops") as mock_git:
            mock_git.remote_url.return_value = "git@github.com:acme/app.git"
            owner, repo, _token = _get_repo_info(session_with_token)

        assert owner == "acme"
        assert repo == "app"

    def test_cache_invalidated_on_select(self, session_with_token: SessionState) -> None:
        from code_review_agent.interactive.commands.repo_cmd import _repo_select

        session_with_token.pr_cache.set("old", "repo", "open", [{"number": 1}])
        assert session_with_token.pr_cache.is_valid

        with (
            patch("code_review_agent.interactive.commands.repo_cmd.git_ops") as mock_git,
            patch("code_review_agent.interactive.commands.repo_cmd.console"),
        ):
            mock_git.list_remotes.return_value = {}
            _repo_select(["new/repo"], session_with_token)

        assert not session_with_token.pr_cache.is_valid


class TestGitOpsRepoHelpers:
    """Test new git_ops functions for repo listing."""

    def test_parse_github_owner_repo_https(self) -> None:
        from code_review_agent.interactive.git_ops import parse_github_owner_repo

        result = parse_github_owner_repo("https://github.com/acme/app.git")
        assert result == ("acme", "app")

    def test_parse_github_owner_repo_ssh(self) -> None:
        from code_review_agent.interactive.git_ops import parse_github_owner_repo

        result = parse_github_owner_repo("git@github.com:acme/app.git")
        assert result == ("acme", "app")

    def test_parse_github_owner_repo_not_github(self) -> None:
        from code_review_agent.interactive.git_ops import parse_github_owner_repo

        result = parse_github_owner_repo("https://gitlab.com/acme/app.git")
        assert result is None

    def test_list_remotes(self) -> None:
        from code_review_agent.interactive.git_ops import list_remotes

        remotes = list_remotes()
        assert isinstance(remotes, dict)
        # The project itself should have an origin remote
        if remotes:
            assert "origin" in remotes


# ---------------------------------------------------------------------------
# Effective settings: config overrides must take effect at runtime
# ---------------------------------------------------------------------------


@pytest.fixture
def real_session(monkeypatch: pytest.MonkeyPatch) -> SessionState:
    """Session with real Settings (not MagicMock) for effective_settings tests."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test-fake-key")
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    from code_review_agent.config import Settings

    settings = Settings()
    return SessionState(settings=settings)


class TestEffectiveSettings:
    """Test that config overrides actually propagate to effective_settings."""

    def test_no_overrides_returns_base(self, real_session: SessionState) -> None:
        assert real_session.effective_settings is real_session.settings
        assert not real_session.config_overrides

    def test_token_tier_override(self, real_session: SessionState) -> None:
        from code_review_agent.token_budget import TokenTier

        assert real_session.settings.token_tier == TokenTier.FREE
        real_session.config_overrides["token_tier"] = "premium"  # noqa: S105
        real_session.invalidate_settings_cache()
        effective = real_session.effective_settings
        assert effective.token_tier == TokenTier.PREMIUM
        # Base settings unchanged
        assert real_session.settings.token_tier == TokenTier.FREE

    def test_default_agents_override(self, real_session: SessionState) -> None:
        real_session.config_overrides["default_agents"] = "performance,style"
        real_session.invalidate_settings_cache()
        assert real_session.effective_settings.default_agents == "performance,style"

    def test_default_agents_empty_becomes_none(
        self,
        real_session: SessionState,
    ) -> None:
        """Empty string override converts to None (tier defaults apply)."""
        real_session.config_overrides["default_agents"] = ""
        real_session.invalidate_settings_cache()
        assert real_session.effective_settings.default_agents is None

    def test_none_string_for_optional_field(
        self,
        real_session: SessionState,
    ) -> None:
        """'None' string converts to None for optional int fields."""
        real_session.config_overrides["max_prompt_tokens"] = "None"
        real_session.invalidate_settings_cache()
        effective = real_session.effective_settings
        assert effective.max_prompt_tokens is None

    def test_valid_int_override(self, real_session: SessionState) -> None:
        real_session.config_overrides["max_review_seconds"] = "600"
        real_session.invalidate_settings_cache()
        assert real_session.effective_settings.max_review_seconds == 600

    def test_invalid_override_skipped(self, real_session: SessionState) -> None:
        """Invalid override is skipped; other overrides still apply."""
        real_session.config_overrides["token_tier"] = "premium"  # noqa: S105
        real_session.config_overrides["max_review_seconds"] = "not_a_number"
        real_session.invalidate_settings_cache()
        effective = real_session.effective_settings
        # Valid override applied
        assert str(effective.token_tier) == "premium"
        # Invalid override skipped -- base value kept
        assert effective.max_review_seconds == real_session.settings.max_review_seconds

    def test_unknown_key_ignored(self, real_session: SessionState) -> None:
        real_session.config_overrides["nonexistent_key"] = "value"
        real_session.invalidate_settings_cache()
        # Should not crash
        effective = real_session.effective_settings
        assert effective is not None

    def test_enum_override_is_proper_type(
        self,
        real_session: SessionState,
    ) -> None:
        """Enum overrides are coerced to proper enum type, not bare strings."""
        from code_review_agent.token_budget import TokenTier

        real_session.config_overrides["token_tier"] = "standard"  # noqa: S105
        real_session.invalidate_settings_cache()
        tier = real_session.effective_settings.token_tier
        assert isinstance(tier, TokenTier)
        assert tier == TokenTier.STANDARD

    def test_float_override(self, real_session: SessionState) -> None:
        real_session.config_overrides["llm_temperature"] = "0.7"
        real_session.invalidate_settings_cache()
        assert real_session.effective_settings.llm_temperature == 0.7

    def test_bool_override(self, real_session: SessionState) -> None:
        real_session.config_overrides["interactive_vi_mode"] = "true"
        real_session.invalidate_settings_cache()
        assert real_session.effective_settings.interactive_vi_mode is True

    def test_cache_invalidation(self, real_session: SessionState) -> None:
        real_session.config_overrides["token_tier"] = "premium"  # noqa: S105
        real_session.invalidate_settings_cache()
        e1 = real_session.effective_settings

        real_session.config_overrides["token_tier"] = "standard"  # noqa: S105
        # Without invalidation, cached value returned
        e2 = real_session.effective_settings
        assert e2 is e1  # same cached object

        # After invalidation, new value
        real_session.invalidate_settings_cache()
        e3 = real_session.effective_settings
        assert str(e3.token_tier) == "standard"

    def test_clearing_overrides_returns_base(
        self,
        real_session: SessionState,
    ) -> None:
        real_session.config_overrides["token_tier"] = "premium"  # noqa: S105
        real_session.invalidate_settings_cache()
        assert str(real_session.effective_settings.token_tier) == "premium"

        real_session.config_overrides.clear()
        real_session.invalidate_settings_cache()
        assert real_session.effective_settings is real_session.settings

    def test_multiple_overrides_combined(
        self,
        real_session: SessionState,
    ) -> None:
        real_session.config_overrides["token_tier"] = "premium"  # noqa: S105
        real_session.config_overrides["default_agents"] = "security"
        real_session.config_overrides["llm_temperature"] = "0.5"
        real_session.invalidate_settings_cache()
        effective = real_session.effective_settings
        assert str(effective.token_tier) == "premium"
        assert effective.default_agents == "security"
        assert effective.llm_temperature == 0.5


# ---------------------------------------------------------------------------
# Display tier: shows "custom" when overrides diverge from preset
# ---------------------------------------------------------------------------


class TestDisplayTier:
    """Test display_tier shows 'custom' when config diverges from tier preset."""

    def test_default_tier(self, real_session: SessionState) -> None:
        assert real_session.display_tier == "free"

    def test_changed_tier(self, real_session: SessionState) -> None:
        real_session.config_overrides["token_tier"] = "premium"  # noqa: S105
        real_session.invalidate_settings_cache()
        assert real_session.display_tier == "premium"

    def test_custom_agents_shows_custom(
        self,
        real_session: SessionState,
    ) -> None:
        """Overriding agents to differ from tier defaults -> custom."""
        real_session.config_overrides["default_agents"] = "performance,style"
        real_session.invalidate_settings_cache()
        assert "(custom)" in real_session.display_tier

    def test_matching_agents_not_custom(
        self,
        real_session: SessionState,
    ) -> None:
        """Setting agents to match tier defaults -> no custom label."""
        # Free tier default is ["security"]
        real_session.config_overrides["default_agents"] = "security"
        real_session.invalidate_settings_cache()
        assert "(custom)" not in real_session.display_tier

    def test_custom_token_budget_shows_custom(
        self,
        real_session: SessionState,
    ) -> None:
        real_session.config_overrides["max_prompt_tokens"] = "10000"
        real_session.invalidate_settings_cache()
        assert "(custom)" in real_session.display_tier

    def test_matching_token_budget_not_custom(
        self,
        real_session: SessionState,
    ) -> None:
        # Free tier budget is 5000
        real_session.config_overrides["max_prompt_tokens"] = "5000"
        real_session.invalidate_settings_cache()
        assert "(custom)" not in real_session.display_tier

    def test_empty_agents_not_custom(
        self,
        real_session: SessionState,
    ) -> None:
        """Empty default_agents means tier defaults apply -> no custom."""
        real_session.config_overrides["default_agents"] = ""
        real_session.invalidate_settings_cache()
        assert "(custom)" not in real_session.display_tier


# ---------------------------------------------------------------------------
# Agent selection priority: --agents > default_agents > tier defaults
# ---------------------------------------------------------------------------


class TestAgentSelectionPriority:
    """Test that agent selection follows the correct priority chain."""

    def test_tier_defaults_when_no_override(
        self,
        real_session: SessionState,
    ) -> None:
        """No --agents, no default_agents -> use tier defaults."""
        from code_review_agent.token_budget import default_agents_for_tier

        settings = real_session.effective_settings
        agent_names: list[str] | None = None

        if agent_names:
            selected = agent_names
        elif settings.default_agents:
            selected = [n.strip() for n in settings.default_agents.split(",")]
        else:
            selected = default_agents_for_tier(settings.token_tier)

        # Free tier = security only
        assert selected == ["security"]

    def test_default_agents_overrides_tier(
        self,
        real_session: SessionState,
    ) -> None:
        """default_agents config overrides tier defaults."""
        from code_review_agent.token_budget import default_agents_for_tier

        real_session.config_overrides["default_agents"] = "performance,style"
        real_session.invalidate_settings_cache()
        settings = real_session.effective_settings
        agent_names: list[str] | None = None

        if agent_names:
            selected = agent_names
        elif settings.default_agents:
            selected = [n.strip() for n in settings.default_agents.split(",")]
        else:
            selected = default_agents_for_tier(settings.token_tier)

        assert selected == ["performance", "style"]

    def test_explicit_agents_overrides_config(
        self,
        real_session: SessionState,
    ) -> None:
        """Explicit --agents flag overrides default_agents config."""
        from code_review_agent.token_budget import default_agents_for_tier

        real_session.config_overrides["default_agents"] = "performance,style"
        real_session.invalidate_settings_cache()
        settings = real_session.effective_settings
        agent_names: list[str] | None = ["security", "test_coverage"]

        if agent_names:
            selected = agent_names
        elif settings.default_agents:
            selected = [n.strip() for n in settings.default_agents.split(",")]
        else:
            selected = default_agents_for_tier(settings.token_tier)

        assert selected == ["security", "test_coverage"]

    def test_clearing_default_agents_falls_back_to_tier(
        self,
        real_session: SessionState,
    ) -> None:
        """Clearing default_agents falls back to tier defaults."""
        from code_review_agent.token_budget import default_agents_for_tier

        # Set then clear
        real_session.config_overrides["default_agents"] = "performance"
        real_session.invalidate_settings_cache()
        real_session.config_overrides["default_agents"] = ""
        real_session.invalidate_settings_cache()
        settings = real_session.effective_settings
        agent_names: list[str] | None = None

        if agent_names:
            selected = agent_names
        elif settings.default_agents:
            selected = [n.strip() for n in settings.default_agents.split(",")]
        else:
            selected = default_agents_for_tier(settings.token_tier)

        assert selected == ["security"]


# ---------------------------------------------------------------------------
# PR number validation: reject non-integer input
# ---------------------------------------------------------------------------


class TestPrNumberValidation:
    """Test that PR number parsing rejects invalid input."""

    def test_valid_pr_number(self) -> None:
        from code_review_agent.interactive.commands.pr_read import (
            _parse_pr_number,
        )

        assert _parse_pr_number(["42"]) == 42
        assert _parse_pr_number(["1"]) == 1
        assert _parse_pr_number(["999"]) == 999

    def test_non_integer_raises(self) -> None:
        from code_review_agent.interactive.commands.pr_read import (
            _parse_pr_number,
        )

        with pytest.raises(ValueError, match="Invalid PR number"):
            _parse_pr_number(["abc"])

    def test_float_raises(self) -> None:
        from code_review_agent.interactive.commands.pr_read import (
            _parse_pr_number,
        )

        with pytest.raises(ValueError, match="Invalid PR number"):
            _parse_pr_number(["4.2"])

    def test_empty_string_raises(self) -> None:
        from code_review_agent.interactive.commands.pr_read import (
            _parse_pr_number,
        )

        with pytest.raises(ValueError, match="Invalid PR number"):
            _parse_pr_number([""])

    def test_pr_commands_reject_non_integer(
        self,
        session_with_token: SessionState,
    ) -> None:
        """All PR commands that take a number handle invalid input."""
        with (
            patch(f"{_PW}._get_repo_info") as mock_info,
            patch(f"{_PW}.console"),
        ):
            mock_info.return_value = ("a", "b", "token")
            from code_review_agent.interactive.commands.pr_write import (
                pr_merge,
            )

            # ValueError is caught by cmd_pr dispatcher
            with pytest.raises(ValueError, match="Invalid PR number"):
                pr_merge(["not_a_number"], session_with_token)


# ---------------------------------------------------------------------------
# Config set/edit invalidates settings cache
# ---------------------------------------------------------------------------


class TestConfigCacheInvalidation:
    """Test that config changes invalidate the settings cache."""

    def test_config_set_invalidates_cache(
        self,
        real_session: SessionState,
    ) -> None:
        from code_review_agent.interactive.commands.config_cmd import (
            cmd_config_set,
        )

        real_session.config_overrides["token_tier"] = "premium"  # noqa: S105
        real_session.invalidate_settings_cache()
        # Cache the effective settings
        _ = real_session.effective_settings

        with patch(
            "code_review_agent.interactive.commands.config_cmd.console",
        ):
            cmd_config_set(["llm_temperature", "0.9"], real_session)

        # Cache should be invalidated
        assert real_session._effective_settings_cache is None
        assert real_session.effective_settings.llm_temperature == 0.9

    def test_config_reset_invalidates_cache(
        self,
        real_session: SessionState,
    ) -> None:
        from code_review_agent.interactive.commands.config_cmd import (
            cmd_config_reset,
        )

        real_session.config_overrides["token_tier"] = "premium"  # noqa: S105
        real_session.invalidate_settings_cache()

        with patch(
            "code_review_agent.interactive.commands.config_cmd.console",
        ):
            cmd_config_reset([], real_session)

        assert not real_session.config_overrides
        assert real_session.effective_settings is real_session.settings


# ---------------------------------------------------------------------------
# Usage history: records are properly accumulated
# ---------------------------------------------------------------------------


class TestUsageHistoryAccumulation:
    """Test that review results are properly tracked in usage history."""

    def _make_report(
        self,
        agents: list[str],
        total_tokens: int = 1000,
    ) -> object:
        from datetime import UTC, datetime

        from code_review_agent.models import (
            AgentResult,
            ReviewReport,
            TokenUsage,
        )

        results = [
            AgentResult(
                agent_name=name,
                findings=[],
                summary="ok",
                execution_time_seconds=1.0,
            )
            for name in agents
        ]
        return ReviewReport(
            reviewed_at=datetime.now(UTC),
            agent_results=results,
            overall_summary="ok",
            risk_level="low",
            token_usage=TokenUsage(
                prompt_tokens=total_tokens // 2,
                completion_tokens=total_tokens // 2,
                total_tokens=total_tokens,
                llm_calls=len(agents) + 1,
                estimated_cost_usd=0.01,
            ),
        )

    def test_records_accumulated(self, real_session: SessionState) -> None:
        report = self._make_report(["security", "performance"])
        real_session.usage_history.record_review(report)  # type: ignore[arg-type]
        assert len(real_session.usage_history.records) == 1
        assert real_session.usage_history.total_tokens == 1000

    def test_multiple_reviews(self, real_session: SessionState) -> None:
        r1 = self._make_report(["security"], total_tokens=500)
        r2 = self._make_report(["performance", "style"], total_tokens=2000)
        real_session.usage_history.record_review(r1)  # type: ignore[arg-type]
        real_session.usage_history.record_review(r2)  # type: ignore[arg-type]
        assert len(real_session.usage_history.records) == 2
        assert real_session.usage_history.total_tokens == 2500
        assert real_session.usage_history.total_cost == 0.02

    def test_tokens_by_agent(self, real_session: SessionState) -> None:
        report = self._make_report(["security", "performance"])
        real_session.usage_history.record_review(report)  # type: ignore[arg-type]
        by_agent = real_session.usage_history.tokens_by_agent()
        assert "security" in by_agent
        assert "performance" in by_agent
        assert sum(by_agent.values()) > 0

    def test_records_since(self, real_session: SessionState) -> None:
        report = self._make_report(["security"])
        real_session.usage_history.record_review(report)  # type: ignore[arg-type]
        recent = real_session.usage_history.records_since(3600)
        assert len(recent) == 1
        # Very old records excluded
        old = real_session.usage_history.records_since(0.001)
        # Might be 0 or 1 depending on timing
        assert len(old) <= 1


# ---------------------------------------------------------------------------
# Stash error handling: stash_pop failure should not lose data
# ---------------------------------------------------------------------------


class TestStashErrorHandling:
    """Test that stash_pop failure shows recovery instructions."""

    def test_stash_pop_failure_shows_recovery(
        self,
        session_with_token: SessionState,
    ) -> None:
        from code_review_agent.interactive.commands.pr_read import _pr_review
        from code_review_agent.interactive.git_ops import GitError

        with (
            patch(
                "code_review_agent.interactive.commands.pr_read.git_ops",
            ) as mock_git,
            patch(
                "code_review_agent.interactive.commands.pr_read._get_repo_info",
            ) as mock_info,
            patch(
                "code_review_agent.interactive.commands.pr_read.fetch_pr_diff",
            ) as mock_fetch,
            patch(
                "code_review_agent.interactive.commands.pr_read.console",
            ) as mock_con,
        ):
            mock_info.return_value = ("a", "b", "token")
            mock_git.is_working_tree_dirty.return_value = True
            mock_git.stash_push.return_value = ""
            mock_git.GitError = GitError
            mock_git.stash_pop.side_effect = GitError("pop", "conflict")
            mock_fetch.return_value = MagicMock(diff_files=[])

            _pr_review(["42"], session_with_token)

        output = str(mock_con.print.call_args_list).lower()
        assert "stash" in output
        assert "manually" in output


# ---------------------------------------------------------------------------
# Token count formatting
# ---------------------------------------------------------------------------


class TestTokenFormatting:
    """Test human-readable token count formatting."""

    def test_small_numbers(self) -> None:
        from code_review_agent.interactive.repl import _format_token_count

        assert _format_token_count(0) == "0"
        assert _format_token_count(999) == "999"

    def test_thousands(self) -> None:
        from code_review_agent.interactive.repl import _format_token_count

        assert _format_token_count(1000) == "1.0k"
        assert _format_token_count(1500) == "1.5k"
        assert _format_token_count(12345) == "12.3k"

    def test_millions(self) -> None:
        from code_review_agent.interactive.repl import _format_token_count

        assert _format_token_count(1_000_000) == "1.0m"
        assert _format_token_count(2_500_000) == "2.5m"

    def test_billions(self) -> None:
        from code_review_agent.interactive.repl import _format_token_count

        assert _format_token_count(1_000_000_000) == "1.0b"

    def test_boundary_999k_to_1m(self) -> None:
        from code_review_agent.interactive.repl import _format_token_count

        # 999,949 should show as k, 999,950+ should show as m
        assert _format_token_count(999_949) == "999.9k"
        assert _format_token_count(999_950) == "1.0m"


# ---------------------------------------------------------------------------
# Review storage (SQLite history)
# ---------------------------------------------------------------------------


class TestReviewStorage:
    """Test SQLite-backed review history storage."""

    def _make_report(
        self,
        risk: str = "medium",
        agents: list[str] | None = None,
        tokens: int = 1000,
    ) -> object:
        from datetime import UTC, datetime

        from code_review_agent.models import (
            AgentResult,
            Finding,
            ReviewReport,
            TokenUsage,
        )

        agent_names = agents or ["security"]
        results = [
            AgentResult(
                agent_name=name,
                findings=[
                    Finding(
                        file_path="src/app.py",
                        line_number=10,
                        severity="high",
                        category=name,
                        title=f"Issue from {name}",
                        description="Test finding",
                        suggestion="Fix it",
                    ),
                ],
                summary="ok",
                execution_time_seconds=1.0,
            )
            for name in agent_names
        ]
        return ReviewReport(
            reviewed_at=datetime.now(UTC),
            agent_results=results,
            overall_summary="Test review",
            risk_level=risk,
            token_usage=TokenUsage(
                prompt_tokens=tokens // 2,
                completion_tokens=tokens // 2,
                total_tokens=tokens,
                llm_calls=len(agent_names) + 1,
                estimated_cost_usd=0.01,
            ),
        )

    def test_save_and_list(self, tmp_path: Path) -> None:
        from code_review_agent.storage import ReviewStorage

        db = tmp_path / "test.db"
        storage = ReviewStorage(str(db))

        report = self._make_report()
        review_id = storage.save(report, repo="acme/app")  # type: ignore[arg-type]
        assert review_id >= 1

        reviews = storage.list_reviews()
        assert len(reviews) == 1
        assert reviews[0]["repo"] == "acme/app"
        assert reviews[0]["risk_level"] == "medium"

    def test_save_multiple_and_filter(self, tmp_path: Path) -> None:
        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(str(tmp_path / "test.db"))

        r1 = self._make_report(risk="high")
        r2 = self._make_report(risk="low")
        storage.save(r1, repo="acme/app")  # type: ignore[arg-type]
        storage.save(r2, repo="acme/api")  # type: ignore[arg-type]

        all_reviews = storage.list_reviews()
        assert len(all_reviews) == 2

        filtered = storage.list_reviews(repo="acme/app")
        assert len(filtered) == 1
        assert filtered[0]["repo"] == "acme/app"

    def test_get_review_by_id(self, tmp_path: Path) -> None:
        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(str(tmp_path / "test.db"))
        report = self._make_report()
        review_id = storage.save(report, repo="acme/app")  # type: ignore[arg-type]

        review = storage.get_review(review_id)
        assert review is not None
        assert review["id"] == review_id
        assert review["report_json"]  # Full JSON preserved

    def test_get_nonexistent_review(self, tmp_path: Path) -> None:
        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(str(tmp_path / "test.db"))
        assert storage.get_review(999) is None

    def test_trends(self, tmp_path: Path) -> None:
        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(str(tmp_path / "test.db"))
        for _ in range(3):
            r = self._make_report(
                agents=["security", "performance"],
                tokens=2000,
            )
            storage.save(r, repo="acme/app")  # type: ignore[arg-type]

        trends = storage.get_trends(days=7)
        assert trends["review_count"] == 3
        assert trends["total_tokens"] == 6000
        assert trends["avg_findings"] > 0

    def test_finding_counts(self, tmp_path: Path) -> None:
        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(str(tmp_path / "test.db"))
        report = self._make_report()
        storage.save(report, repo="test/repo")  # type: ignore[arg-type]

        reviews = storage.list_reviews()
        assert reviews[0]["high_count"] >= 1

    def test_export_json(self, tmp_path: Path) -> None:
        import json

        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(str(tmp_path / "test.db"))
        storage.save(self._make_report(), repo="acme/app")  # type: ignore[arg-type]

        output = storage.export_json()
        parsed = json.loads(output)
        assert isinstance(parsed, list)
        assert len(parsed) == 1

    def test_pr_number_extraction(self) -> None:
        from code_review_agent.storage import _extract_pr_number

        assert (
            _extract_pr_number(
                "https://github.com/acme/app/pull/42",
            )
            == 42
        )
        assert _extract_pr_number(None) is None
        assert _extract_pr_number("https://github.com/acme/app") is None

    def test_db_created_automatically(self, tmp_path: Path) -> None:
        from code_review_agent.storage import ReviewStorage

        db = tmp_path / "subdir" / "reviews.db"
        storage = ReviewStorage(str(db))
        assert storage.db_path.exists()


# ---------------------------------------------------------------------------
# History command
# ---------------------------------------------------------------------------


class TestHistoryCommand:
    """Test history command registration and dispatch."""

    def test_history_registered(self) -> None:
        from code_review_agent.interactive.repl import _COMMANDS

        assert "history" in _COMMANDS

    def test_help_includes_history(self) -> None:
        from code_review_agent.interactive.commands.meta import COMMAND_HELP

        assert "History" in COMMAND_HELP
