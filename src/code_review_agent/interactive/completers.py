"""Completers for the interactive REPL."""

from __future__ import annotations

from typing import TYPE_CHECKING

from prompt_toolkit.completion import (
    CompleteEvent,
    Completer,
    Completion,
    NestedCompleter,
    PathCompleter,
)
from prompt_toolkit.document import Document

if TYPE_CHECKING:
    from collections.abc import Iterable


class _ReplCompleter(Completer):
    """Completer that uses PathCompleter for ``cd`` and NestedCompleter for everything else."""

    def __init__(self, nested: NestedCompleter) -> None:
        self._nested = nested
        self._path = PathCompleter(only_directories=True, expanduser=True)

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterable[Completion]:
        text = document.text_before_cursor.lstrip()

        if text.startswith("cd "):
            # Complete the path portion after "cd "
            path_text = text[3:]
            path_doc = Document(path_text, len(path_text))
            yield from self._path.get_completions(path_doc, complete_event)
        else:
            yield from self._nested.get_completions(document, complete_event)


def build_static_completer() -> _ReplCompleter:
    """Build the command completer with path completion for ``cd``."""
    nested = NestedCompleter.from_nested_dict(
        {
            "status": None,
            "diff": {
                "staged": None,
            },
            "log": {
                "-n": None,
                "--graph": None,
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
            "cd": None,
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
    return _ReplCompleter(nested)
