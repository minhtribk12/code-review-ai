"""Tests for the interactive REPL module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from code_review_agent.interactive.commands.config_cmd import (
    _mask_secret,
    cmd_config_get,
    cmd_config_reset,
    cmd_config_set,
)
from code_review_agent.interactive.commands.meta import cmd_help, cmd_version
from code_review_agent.interactive.repl import _dispatch
from code_review_agent.interactive.session import SessionState


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
