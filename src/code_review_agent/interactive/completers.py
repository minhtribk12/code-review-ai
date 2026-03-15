"""Dynamic completers for the interactive REPL."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from prompt_toolkit.completion import Completer, Completion, NestedCompleter

from code_review_agent.interactive import git_ops

if TYPE_CHECKING:
    from prompt_toolkit.document import Document


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
            "review": {
                "staged": None,
                "--agents": None,
                "--format": {
                    "rich": None,
                    "json": None,
                },
            },
            "config": {
                "show": None,
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
            "usage": None,
            "help": {
                "git": None,
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


class DynamicBranchCompleter(Completer):
    """Completer that provides branch names from git."""

    def __init__(self, cache_ttl: float = 5.0) -> None:
        self._cache: list[str] = []
        self._cache_time: float = 0
        self._ttl = cache_ttl

    def get_completions(self, document: Document, complete_event: object) -> list[Completion]:
        word = document.get_word_before_cursor()
        branches = self._get_branches()
        return [Completion(b, start_position=-len(word)) for b in branches if b.startswith(word)]

    def _get_branches(self) -> list[str]:
        now = time.monotonic()
        if now - self._cache_time > self._ttl:
            try:
                output = git_ops.list_branches()
                self._cache = [b.strip() for b in output.splitlines() if b.strip()]
            except git_ops.GitError:
                self._cache = []
            self._cache_time = now
        return self._cache
