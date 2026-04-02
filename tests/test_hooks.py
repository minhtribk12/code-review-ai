"""Tests for the hook system."""

from __future__ import annotations

import os
import stat
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from code_review_agent.interactive.hooks import (
    HookConfig,
    HookEvent,
    load_hooks,
    run_hook,
    run_hooks_for_event,
)


class TestLoadHooks:
    """Test loading hook configurations from YAML."""

    def test_no_files(self, tmp_path: Path) -> None:
        result = load_hooks(user_dir=tmp_path)
        assert result == []

    def test_parses_yaml(self, tmp_path: Path) -> None:
        hooks_file = tmp_path / "hooks.yaml"
        hooks_file.write_text(
            "hooks:\n"
            "  pre_review:\n"
            "    - command: echo ok\n"
            "      timeout: 3\n"
            "  post_review:\n"
            "    - command: echo done\n"
        )
        result = load_hooks(user_dir=tmp_path)
        assert len(result) == 2
        assert result[0].event == HookEvent.PRE_REVIEW
        assert result[0].command == "echo ok"
        assert result[0].timeout_seconds == 3
        assert result[1].event == HookEvent.POST_REVIEW

    def test_merges_user_and_project(self, tmp_path: Path) -> None:
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        (user_dir / "hooks.yaml").write_text("hooks:\n  pre_review:\n    - command: user_hook\n")
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "hooks.yaml").write_text(
            "hooks:\n  pre_review:\n    - command: project_hook\n"
        )
        result = load_hooks(user_dir=user_dir, project_dir=project_dir)
        assert len(result) == 2
        assert result[0].command == "user_hook"
        assert result[1].command == "project_hook"

    def test_unknown_event_skipped(self, tmp_path: Path) -> None:
        hooks_file = tmp_path / "hooks.yaml"
        hooks_file.write_text("hooks:\n  unknown_event:\n    - command: echo x\n")
        result = load_hooks(user_dir=tmp_path)
        assert result == []


class TestRunHook:
    """Test executing individual hooks."""

    def test_exit_0_allowed(self) -> None:
        hook = HookConfig(
            event=HookEvent.PRE_REVIEW, command='echo \'{"allowed": true, "message": "ok"}\''
        )
        result = run_hook(hook, {})
        assert result.is_allowed is True
        assert result.message == "ok"

    def test_exit_2_blocked(self) -> None:
        hook = HookConfig(event=HookEvent.PRE_REVIEW, command="exit 2")
        result = run_hook(hook, {})
        assert result.is_allowed is False

    def test_timeout_failsafe(self) -> None:
        hook = HookConfig(event=HookEvent.PRE_REVIEW, command="sleep 10", timeout_seconds=1)
        result = run_hook(hook, {})
        assert result.is_allowed is True
        assert "timed out" in result.message

    def test_invalid_json_failsafe(self) -> None:
        hook = HookConfig(event=HookEvent.PRE_REVIEW, command="echo not-json")
        result = run_hook(hook, {})
        assert result.is_allowed is True

    def test_nonzero_exit_failsafe(self) -> None:
        hook = HookConfig(event=HookEvent.PRE_REVIEW, command="exit 1")
        result = run_hook(hook, {})
        assert result.is_allowed is True

    def test_receives_context_on_stdin(self, tmp_path: Path) -> None:
        script = tmp_path / "hook.sh"
        output = tmp_path / "output.txt"
        script.write_text(f"#!/bin/bash\ncat > {output}\necho '{{\"allowed\": true}}'\n")
        os.chmod(str(script), stat.S_IRWXU)
        hook = HookConfig(event=HookEvent.PRE_REVIEW, command=str(script))
        run_hook(hook, {"repo": "test/repo"})
        content = output.read_text()
        assert "test/repo" in content


class TestRunHooksForEvent:
    """Test running multiple hooks for an event."""

    def test_runs_all_when_allowed(self) -> None:
        hooks = [
            HookConfig(event=HookEvent.PRE_REVIEW, command="echo ok"),
            HookConfig(event=HookEvent.PRE_REVIEW, command="echo ok2"),
        ]
        results = run_hooks_for_event(HookEvent.PRE_REVIEW, {}, hooks)
        assert len(results) == 2
        assert all(r.is_allowed for r in results)

    def test_stops_on_first_block(self) -> None:
        hooks = [
            HookConfig(event=HookEvent.PRE_REVIEW, command="exit 2"),
            HookConfig(event=HookEvent.PRE_REVIEW, command="echo should-not-run"),
        ]
        results = run_hooks_for_event(HookEvent.PRE_REVIEW, {}, hooks)
        assert len(results) == 1
        assert not results[0].is_allowed

    def test_filters_by_event(self) -> None:
        hooks = [
            HookConfig(event=HookEvent.PRE_REVIEW, command="echo pre"),
            HookConfig(event=HookEvent.POST_REVIEW, command="echo post"),
        ]
        results = run_hooks_for_event(HookEvent.PRE_REVIEW, {}, hooks)
        assert len(results) == 1
