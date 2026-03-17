"""Completers for the interactive REPL."""

from __future__ import annotations

from prompt_toolkit.completion import NestedCompleter


def build_static_completer() -> NestedCompleter:
    """Build the static command completer tree."""
    return NestedCompleter.from_nested_dict(
        {
            "status": None,
            "diff": {
                "staged": None,
            },
            "log": {
                "-n": None,
            },
            "show": None,
            "branch": {
                "switch": None,
                "create": None,
                "delete": {"--force": None},
                "rename": None,
                "-r": None,
            },
            "add": None,
            "unstage": None,
            "commit": {"-m": None},
            "stash": {"pop": None, "list": None},
            "pr": {
                "list": {"--state": {"open": None, "closed": None, "all": None}},
                "show": None,
                "diff": None,
                "checks": None,
                "checkout": None,
                "review": {"--agents": None, "--format": {"rich": None, "json": None}},
                "create": {
                    "--title": None,
                    "--body": None,
                    "--base": None,
                    "--draft": None,
                    "--fill": None,
                    "--dry-run": None,
                },
                "merge": {
                    "--strategy": {"merge": None, "squash": None, "rebase": None},
                    "--dry-run": None,
                },
                "approve": {"-m": None, "--dry-run": None},
                "request-changes": {"-m": None, "--dry-run": None},
                "mine": None,
                "assigned": None,
                "stale": {"--days": None},
                "ready": None,
                "conflicts": None,
                "summary": {"--full": None},
                "unresolved": None,
            },
            "review": {
                "staged": None,
                "--agents": None,
                "--format": {
                    "rich": None,
                    "json": None,
                },
            },
            "repo": {
                "list": {"--limit": None},
                "select": None,
                "current": None,
                "clear": None,
            },
            "config": {
                "show": None,
                "edit": None,
                "get": None,
                "set": None,
                "save": None,
                "reset": None,
                "validate": None,
                "diff": None,
                "llm": None,
                "budget": None,
                "review": None,
                "github": None,
            },
            "watch": {"--interval": None, "--agents": None},
            "history": {
                "list": {"--repo": None, "--days": None, "--limit": None},
                "show": None,
                "trends": {"--repo": None, "--days": None},
                "export": {"--repo": None, "--format": {"json": None}},
            },
            "findings": None,
            "usage": None,
            "help": {
                "git": None,
                "pr": None,
                "review": None,
                "config": None,
                "usage": None,
                "meta": None,
            },
            "agents": None,
            "version": None,
            "clear": None,
            "exit": None,
        }
    )
