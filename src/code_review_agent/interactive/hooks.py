"""Hook system: run user-defined scripts at review lifecycle events.

Hook protocol (inspired by Claude Code):
- Hooks receive JSON context on stdin
- Hooks return JSON on stdout: {"allowed": true/false, "message": "..."}
- Exit code 0 = allowed, exit code 2 = blocked, any other = allowed (fail-safe)
- Errors and timeouts default to allowed (hooks never break the tool)
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import structlog
import yaml

logger = structlog.get_logger(__name__)


class HookEvent(StrEnum):
    """Lifecycle events that can trigger hooks."""

    PRE_REVIEW = "pre_review"
    POST_REVIEW = "post_review"
    PRE_COMMENT = "pre_comment"
    POST_COMMENT = "post_comment"


@dataclass(frozen=True)
class HookConfig:
    """Configuration for a single hook."""

    event: HookEvent
    command: str
    timeout_seconds: int = 5


@dataclass(frozen=True)
class HookResult:
    """Result from executing a hook."""

    is_allowed: bool
    message: str
    hook_name: str


def load_hooks(
    user_dir: Path | None = None,
    project_dir: Path | None = None,
) -> list[HookConfig]:
    """Load hooks from user and project directories.

    Both sources are merged: user hooks run first, project hooks run last.
    """
    configs: list[HookConfig] = []

    for source_dir in [user_dir or Path("~/.cra").expanduser(), project_dir]:
        if source_dir is None:
            continue
        hooks_file = source_dir / "hooks.yaml"
        if not hooks_file.is_file():
            continue
        try:
            raw = yaml.safe_load(hooks_file.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                continue
            hooks_section = raw.get("hooks", {})
            if not isinstance(hooks_section, dict):
                continue
            for event_name, hook_list in hooks_section.items():
                try:
                    event = HookEvent(event_name)
                except ValueError:
                    logger.debug(f"unknown hook event {event_name}, skipping")
                    continue
                if not isinstance(hook_list, list):
                    continue
                for entry in hook_list:
                    if not isinstance(entry, dict) or "command" not in entry:
                        continue
                    configs.append(
                        HookConfig(
                            event=event,
                            command=str(entry["command"]),
                            timeout_seconds=int(entry.get("timeout", 5)),
                        )
                    )
        except Exception:
            logger.debug(f"failed to load hooks from {hooks_file}", exc_info=True)

    return configs


def run_hook(hook: HookConfig, context: dict[str, object]) -> HookResult:
    """Execute a single hook command.

    Fail-safe: errors and timeouts always return allowed=True.
    """
    hook_name = hook.command.split("/")[-1] if "/" in hook.command else hook.command
    try:
        result = subprocess.run(  # noqa: S602 - hooks must execute user-defined shell commands
            hook.command,
            shell=True,
            input=json.dumps(context),
            capture_output=True,
            text=True,
            timeout=hook.timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        logger.debug(f"hook {hook_name} timed out after {hook.timeout_seconds}s")
        return HookResult(
            is_allowed=True, message=f"Hook timed out: {hook_name}", hook_name=hook_name
        )
    except Exception as exc:
        logger.debug(f"hook {hook_name} failed: {exc}")
        return HookResult(is_allowed=True, message=f"Hook error: {hook_name}", hook_name=hook_name)

    # Exit code 2 = blocked, anything else = allowed
    if result.returncode == 2:
        message = _parse_message(result.stdout, default=f"Blocked by hook: {hook_name}")
        return HookResult(is_allowed=False, message=message, hook_name=hook_name)

    if result.returncode != 0:
        logger.debug(f"hook {hook_name} exited with code {result.returncode}")
        return HookResult(is_allowed=True, message="", hook_name=hook_name)

    # Parse JSON output for message
    message = _parse_message(result.stdout, default="")
    is_allowed = _parse_allowed(result.stdout, default=True)
    return HookResult(is_allowed=is_allowed, message=message, hook_name=hook_name)


def run_hooks_for_event(
    event: HookEvent,
    context: dict[str, object],
    hooks: list[HookConfig],
) -> list[HookResult]:
    """Run all hooks for an event. Stops on first block."""
    results: list[HookResult] = []
    for hook in hooks:
        if hook.event != event:
            continue
        result = run_hook(hook, context)
        results.append(result)
        if not result.is_allowed:
            break
    return results


def _parse_message(stdout: str, default: str) -> str:
    """Extract message from hook JSON output."""
    try:
        data = json.loads(stdout)
        if isinstance(data, dict):
            return str(data.get("message", default))
    except (json.JSONDecodeError, ValueError):
        pass
    return default


def _parse_allowed(stdout: str, default: bool) -> bool:
    """Extract allowed flag from hook JSON output."""
    try:
        data = json.loads(stdout)
        if isinstance(data, dict) and "allowed" in data:
            return bool(data["allowed"])
    except (json.JSONDecodeError, ValueError):
        pass
    return default
