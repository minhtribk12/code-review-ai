"""Interactive git graph navigator.

Full-screen TUI for browsing git commit history as a branch graph.
Navigate with arrows, preview with Space, checkout with Enter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from prompt_toolkit import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    HSplit,
    Window,
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.layout import Layout
from rich.console import Console

from code_review_agent.interactive import git_ops
from code_review_agent.theme import theme

if TYPE_CHECKING:
    from prompt_toolkit.key_binding import KeyPressEvent
_RE_HASH = re.compile(r"([0-9a-f]{7,12})\b")
_RE_REFS = re.compile(r"\(([^)]+)\)")

_Lines = list[tuple[str, str]]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class GraphMode(StrEnum):
    NAVIGATE = "navigate"
    DETAIL = "detail"
    CONFIRM = "confirm"


@dataclass
class GraphCommit:
    """A navigable commit in the graph."""

    hash: str
    refs: str
    subject: str
    line_index: int


@dataclass
class GraphState:
    """State for the interactive graph navigator."""

    raw_lines: list[str] = field(default_factory=list)
    commits: list[GraphCommit] = field(default_factory=list)
    cursor: int = 0
    mode: GraphMode = GraphMode.NAVIGATE
    detail_text: str = ""
    status_message: str = ""
    count: int = 30

    @property
    def current_commit(self) -> GraphCommit | None:
        if not self.commits or self.cursor >= len(self.commits):
            return None
        return self.commits[self.cursor]

    def move_up(self) -> None:
        if self.cursor > 0:
            self.cursor -= 1

    def move_down(self) -> None:
        if self.cursor < len(self.commits) - 1:
            self.cursor += 1

    def toggle_detail(self) -> None:
        if self.mode == GraphMode.DETAIL:
            self.mode = GraphMode.NAVIGATE
            self.detail_text = ""
            return
        commit = self.current_commit
        if commit is None:
            return
        try:
            self.detail_text = git_ops.show_commit(commit.hash)
            self.mode = GraphMode.DETAIL
        except git_ops.GitError as exc:
            self.status_message = f"! {exc}"

    def request_checkout(self) -> None:
        if self.current_commit is None:
            return
        self.mode = GraphMode.CONFIRM

    def confirm_checkout(self) -> None:
        commit = self.current_commit
        if commit is None:
            self.mode = GraphMode.NAVIGATE
            return
        try:
            git_ops.checkout_ref(commit.hash)
            self.status_message = f"Checked out {commit.hash}"
        except git_ops.GitError as exc:
            self.status_message = f"! Checkout failed: {exc}"
        self.mode = GraphMode.NAVIGATE

    def cancel_confirm(self) -> None:
        self.mode = GraphMode.NAVIGATE


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_graph(raw: str) -> tuple[list[str], list[GraphCommit]]:
    """Parse git log --graph output into raw lines and commits."""
    lines = raw.strip().splitlines()
    commits: list[GraphCommit] = []

    for i, line in enumerate(lines):
        if "*" not in line:
            continue
        # Extract content after the graph prefix
        star_pos = line.index("*")
        content = line[star_pos + 1 :].strip()

        hash_match = _RE_HASH.match(content)
        if not hash_match:
            continue

        commit_hash = hash_match.group(1)
        rest = content[hash_match.end() :].strip()

        refs = ""
        ref_match = _RE_REFS.match(rest)
        if ref_match:
            refs = ref_match.group(0)
            rest = rest[ref_match.end() :].strip()

        subject = rest.split("(")[0].strip() if "(" in rest else rest.strip()

        commits.append(
            GraphCommit(
                hash=commit_hash,
                refs=refs,
                subject=subject,
                line_index=i,
            )
        )

    return lines, commits


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _render_header(state: GraphState) -> _Lines:
    """Render the header with title and key hints."""
    n_commits = len(state.commits)
    lines: _Lines = [
        ("bold", f" Git Graph ({n_commits} commits)\n"),
    ]

    if state.mode == GraphMode.DETAIL:
        lines.append((theme.muted, " [Space] close  [q] quit\n"))
    elif state.mode == GraphMode.CONFIRM:
        lines.append((theme.muted, " [y] checkout  [n] cancel\n"))
    else:
        lines.append((theme.muted, " [Space] detail  [Enter] checkout  [q] quit\n"))

    if state.status_message:
        style = theme.error if state.status_message.startswith("!") else theme.success
        lines.append((style, f" {state.status_message}\n"))

    return lines


def _render_graph(state: GraphState) -> _Lines:
    """Render the graph with cursor highlighting."""
    lines: _Lines = []

    if not state.raw_lines:
        lines.append((theme.muted, "  No commits.\n"))
        return lines

    # Map commit line_index to commit position for cursor
    commit_line_map: dict[int, int] = {}
    for ci, c in enumerate(state.commits):
        commit_line_map[c.line_index] = ci

    # Viewport: center cursor
    cursor_line = state.commits[state.cursor].line_index if state.commits else 0
    viewport = 20 if state.mode == GraphMode.NAVIGATE else 8
    half = viewport // 2
    start = max(0, cursor_line - half)
    end = min(len(state.raw_lines), start + viewport)

    for i in range(start, end):
        line = state.raw_lines[i]
        is_cursor_commit = i in commit_line_map and commit_line_map[i] == state.cursor

        # Prefix
        if is_cursor_commit:
            lines.append((theme.graph_cursor, " >"))
        else:
            lines.append(("", "  "))

        # Colorize graph characters
        if "*" not in line:
            _append_graph_chars(lines, line)
        else:
            star_pos = line.index("*")
            graph_part = line[:star_pos]
            _append_graph_chars(lines, graph_part)

            # Star colored by branch column
            col = sum(1 for ch in graph_part if ch in ("|", "/", "\\", "_"))
            branches = theme.graph_branches
            star_color = branches[col % len(branches)]
            lines.append((f"bold {star_color}", "*"))

            # Content: hash + refs
            content = line[star_pos + 1 :].strip()
            hash_match = _RE_HASH.match(content)
            if hash_match:
                commit_hash = hash_match.group(1)
                rest = content[hash_match.end() :].strip()

                hash_style = f"bold {theme.graph_hash}" if is_cursor_commit else theme.graph_hash
                lines.append((hash_style, f" {commit_hash}"))

                ref_match = _RE_REFS.match(rest)
                if ref_match:
                    lines.append(("", " "))
                    _append_refs(lines, ref_match.group(0))
            else:
                lines.append(("", f" {content}"))

        lines.append(("", "\n"))

    return lines


def _render_status_bar(state: GraphState) -> _Lines:
    """Render the bottom status bar with current commit info."""
    lines: _Lines = [(theme.muted, " " + "-" * 76 + "\n")]

    commit = state.current_commit
    if commit is None:
        return lines

    try:
        info = git_ops.show_commit_oneline(commit.hash)
        lines.append(("", f" {info}\n"))
    except git_ops.GitError:
        lines.append((theme.muted, f" {commit.hash}\n"))

    return lines


def _render_detail(state: GraphState) -> _Lines:
    """Render the detail panel with full commit content."""
    if not state.detail_text:
        return []

    lines: _Lines = [
        (theme.muted, " " + "=" * 76 + "\n"),
    ]

    for raw_line in state.detail_text.splitlines()[:60]:
        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            lines.append((theme.graph_diff_add, f" {raw_line}\n"))
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            lines.append((theme.graph_diff_del, f" {raw_line}\n"))
        elif raw_line.startswith("@@"):
            lines.append((theme.graph_diff_hunk, f" {raw_line}\n"))
        elif raw_line.startswith("diff ") or raw_line.startswith("index "):
            lines.append((theme.muted, f" {raw_line}\n"))
        elif raw_line.startswith("commit "):
            lines.append((f"bold {theme.graph_hash}", f" {raw_line}\n"))
        elif raw_line.startswith("Author:") or raw_line.startswith("Date:"):
            lines.append((theme.muted, f" {raw_line}\n"))
        else:
            lines.append(("", f" {raw_line}\n"))

    return lines


def _render_confirm(state: GraphState) -> _Lines:
    """Render the checkout confirmation panel."""
    commit = state.current_commit
    if commit is None:
        return []

    try:
        info = git_ops.show_commit_oneline(commit.hash)
    except git_ops.GitError:
        info = commit.hash

    lines: _Lines = [
        (theme.muted, " " + "=" * 60 + "\n"),
        ("", "\n"),
        (theme.warning, "   CHECKOUT commit "),
        ("bold", f"{commit.hash}\n"),
        (theme.muted, "   This will detach HEAD from the current branch.\n"),
        ("", "\n"),
        (theme.muted, f"   {info}\n"),
        ("", "\n"),
        (theme.accent, "   [y]"),
        ("bold", " Checkout    "),
        (theme.muted, "[n]"),
        ("", " Cancel\n"),
        ("", "\n"),
        (theme.muted, " " + "=" * 60 + "\n"),
    ]
    return lines


def _render_footer(state: GraphState) -> _Lines:
    """Mode-aware footer."""
    lines: _Lines = [(theme.muted, " " + "-" * 76 + "\n")]
    if state.mode == GraphMode.CONFIRM:
        lines.extend(
            [
                (theme.accent, " [y]"),
                ("", "es "),
                (theme.muted, "[n]"),
                ("", "o\n"),
            ]
        )
    elif state.mode == GraphMode.DETAIL:
        lines.extend(
            [
                (theme.accent, " [Space]"),
                ("", " close "),
                (theme.muted, "[q]"),
                ("", " quit\n"),
            ]
        )
    else:
        lines.extend(
            [
                (theme.accent, " [Space]"),
                ("", " detail "),
                (theme.accent, "[Enter]"),
                ("", " checkout "),
                (theme.muted, "[q]"),
                ("", " quit\n"),
            ]
        )
    return lines


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _append_graph_chars(lines: _Lines, text: str) -> None:
    """Append graph characters with column-based coloring."""
    branches = theme.graph_branches
    col = 0
    for ch in text:
        if ch in ("|", "/", "\\", "_"):
            color = branches[col % len(branches)]
            lines.append((color, ch))
            col += 1
        else:
            lines.append(("", ch))


def _append_refs(lines: _Lines, refs_str: str) -> None:
    """Append colorized refs using theme colors."""
    inner = refs_str[1:-1]
    lines.append((theme.graph_ref_paren, "("))
    parts = [p.strip() for p in inner.split(",")]
    for i, part in enumerate(parts):
        if i > 0:
            lines.append((theme.muted, ", "))
        if part.startswith("HEAD"):
            lines.append((theme.graph_ref_head, part))
        elif part.startswith("tag:"):
            lines.append((theme.graph_ref_tag, part))
        elif "/" in part:
            lines.append((theme.graph_ref_remote, part))
        else:
            lines.append((theme.graph_ref_local, part))
    lines.append((theme.graph_ref_paren, ")"))


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


def run_graph_app(*, count: int = 30, branches: list[str] | None = None) -> None:
    """Launch the interactive graph navigator."""
    raw = git_ops.log_graph(count=count, branches=branches)
    if not raw.strip():
        Console().print("[dim]No commits.[/dim]")
        return

    raw_lines, commits = _parse_graph(raw)
    if not commits:
        Console().print("[dim]No commits found in graph.[/dim]")
        return

    state = GraphState(
        raw_lines=raw_lines,
        commits=commits,
        count=count,
    )

    # Controls
    header_ctl = FormattedTextControl(lambda: _render_header(state))
    graph_ctl = FormattedTextControl(lambda: _render_graph(state))

    def _detail_or_confirm() -> _Lines:
        if state.mode == GraphMode.CONFIRM:
            return _render_confirm(state)
        if state.mode == GraphMode.DETAIL:
            return _render_detail(state)
        return _render_status_bar(state)

    detail_ctl = FormattedTextControl(_detail_or_confirm)

    is_expanded = Condition(
        lambda: state.mode in (GraphMode.DETAIL, GraphMode.CONFIRM),
    )

    layout = Layout(
        HSplit(
            [
                Window(header_ctl, height=3, wrap_lines=True),
                Window(graph_ctl, wrap_lines=False),
                ConditionalContainer(
                    Window(
                        detail_ctl,
                        height=Dimension(min=6, max=25),
                        wrap_lines=True,
                    ),
                    filter=is_expanded,
                ),
                ConditionalContainer(
                    Window(
                        FormattedTextControl(lambda: _render_status_bar(state)),
                        height=2,
                    ),
                    filter=~is_expanded,
                ),
                Window(
                    FormattedTextControl(lambda: _render_footer(state)),
                    height=2,
                ),
            ]
        ),
    )

    # Key bindings
    kb = KeyBindings()

    @kb.add("up")
    @kb.add("k")
    def _up(_e: KeyPressEvent) -> None:
        if state.mode == GraphMode.NAVIGATE:
            state.move_up()

    @kb.add("down")
    @kb.add("j")
    def _down(_e: KeyPressEvent) -> None:
        if state.mode == GraphMode.NAVIGATE:
            state.move_down()

    @kb.add("space")
    def _space(_e: KeyPressEvent) -> None:
        if state.mode in (GraphMode.NAVIGATE, GraphMode.DETAIL):
            state.toggle_detail()

    @kb.add("enter")
    def _enter(_e: KeyPressEvent) -> None:
        if state.mode == GraphMode.NAVIGATE:
            state.request_checkout()

    @kb.add("y")
    def _yes(_e: KeyPressEvent) -> None:
        if state.mode == GraphMode.CONFIRM:
            state.confirm_checkout()

    @kb.add("n")
    def _no(_e: KeyPressEvent) -> None:
        if state.mode == GraphMode.CONFIRM:
            state.cancel_confirm()

    @kb.add("q")
    @kb.add("escape")
    def _quit(event: KeyPressEvent) -> None:
        if state.mode == GraphMode.DETAIL:
            state.mode = GraphMode.NAVIGATE
            state.detail_text = ""
        elif state.mode == GraphMode.CONFIRM:
            state.cancel_confirm()
        else:
            event.app.exit()

    app: Application[None] = Application(
        layout=layout,
        key_bindings=kb,
        full_screen=True,
        refresh_interval=0.1,
    )
    app.run()
