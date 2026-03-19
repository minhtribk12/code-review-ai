"""Full-screen cleanup panel: remove all tool-generated files from ~/.cra/."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout

if TYPE_CHECKING:
    from prompt_toolkit.key_binding import KeyPressEvent

_CRA_DIR = Path("~/.cra").expanduser()
_HISTORY_FILE = Path("~/.cra_history").expanduser()

# Files and directories managed by the tool inside ~/.cra/
_MANAGED_ITEMS: list[tuple[str, str]] = [
    ("config.yaml", "Configuration overrides"),
    ("secrets.env", "API keys for all providers"),
    ("providers.yaml", "User-defined provider overrides"),
    ("providers.json", "Legacy provider file"),
    ("reviews.db", "Review history and findings"),
    ("reviews.db-shm", "SQLite shared memory"),
    ("reviews.db-wal", "SQLite write-ahead log"),
    ("agents", "Custom agent definitions"),
]

# Project-level files the tool reads but user owns
_ENV_FILE = Path.cwd() / ".env"


def _scan_items() -> list[tuple[Path, str, str]]:
    """Scan for existing tool-managed files.

    Returns list of (path, name, description) for items that exist.
    """
    found: list[tuple[Path, str, str]] = []
    for name, desc in _MANAGED_ITEMS:
        path = _CRA_DIR / name
        if path.exists():
            if path.is_dir():
                count = sum(1 for _ in path.rglob("*") if _.is_file())
                desc = f"{desc} ({count} file(s))"
            else:
                size = path.stat().st_size
                desc = f"{desc} ({_human_size(size)})"
            found.append((path, name, desc))

    if _HISTORY_FILE.is_file():
        size = _HISTORY_FILE.stat().st_size
        desc = f"REPL command history ({_human_size(size)})"
        found.append((_HISTORY_FILE, "~/.cra_history", desc))

    return found


def _human_size(size_bytes: int) -> str:
    """Format byte count as human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


class _CleanPanel:
    """State for the cleanup confirmation panel."""

    def __init__(self) -> None:
        self.items = _scan_items()
        self.has_env = _ENV_FILE.is_file()
        self.confirmed: bool = False
        self.done: bool = False
        self.error: str = ""
        self.deleted_count: int = 0

    def confirm(self) -> None:
        """Delete all managed items."""
        count = 0
        for path, _name, _desc in self.items:
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                count += 1
            except OSError:
                self.error = f"Failed to remove: {path}"

        # Remove ~/.cra/ directory itself if empty
        if _CRA_DIR.is_dir():
            try:
                _CRA_DIR.rmdir()
                count += 1
            except OSError:
                pass

        self.deleted_count = count
        self.confirmed = True
        self.done = True

    def render(self) -> FormattedText:
        lines: list[tuple[str, str]] = []

        lines.append(("bold", " Cleanup Tool Data\n"))
        lines.append(("", "\n"))

        if self.done:
            return self._render_done(lines)

        if not self.items:
            lines.append(("green", "  Nothing to clean. No tool files found.\n"))
            lines.append(("", "\n"))
            if self.has_env:
                lines.append(("dim", "  Note: .env exists but is not managed by this tool.\n"))
            lines.append(("", "\n"))
            lines.append(("", "  "))
            lines.append(("cyan", "q"))
            lines.append(("", " quit\n"))
            return FormattedText(lines)

        lines.append(("bold red", "  The following files will be permanently deleted:\n"))
        lines.append(("", "\n"))

        for _path, name, desc in self.items:
            lines.append(("red", "    x "))
            lines.append(("bold", f"{name:<24}"))
            lines.append(("dim", f"{desc}\n"))

        if _CRA_DIR.is_dir():
            lines.append(("red", "    x "))
            lines.append(("bold", f"{'~/.cra/':<24}"))
            lines.append(("dim", "Directory (if empty after cleanup)\n"))

        lines.append(("", "\n"))

        # Warning about .env
        if self.has_env:
            lines.append(("bold yellow", "  ! "))
            lines.append(("yellow", f"Project .env file exists at: {_ENV_FILE}\n"))
            lines.append(("yellow", "    NOT managed by the tool and will NOT be deleted.\n"))
            lines.append(("yellow", "    It may contain API keys you set manually.\n"))
        else:
            lines.append(("dim", "  No project .env file found.\n"))

        lines.append(("", "\n"))
        lines.append(("bold", "  This action cannot be undone.\n"))
        lines.append(("", "\n"))
        lines.append(("", "  "))
        lines.append(("cyan", "y"))
        lines.append(("", " confirm delete, "))
        lines.append(("cyan", "q/Esc"))
        lines.append(("", " cancel\n"))

        return FormattedText(lines)

    def _render_done(self, lines: list[tuple[str, str]]) -> FormattedText:
        if self.error:
            lines.append(("red", f"  {self.error}\n"))
            lines.append(("", "\n"))

        lines.append(("green", f"  Cleaned up {self.deleted_count} item(s).\n"))
        lines.append(("", "\n"))

        if self.has_env:
            lines.append(("yellow", f"  Reminder: project .env still exists at {_ENV_FILE}\n"))
            lines.append(("dim", "  Remove it manually if no longer needed.\n"))

        lines.append(("", "\n"))
        lines.append(("", "  "))
        lines.append(("cyan", "q/Esc"))
        lines.append(("", " close\n"))

        return FormattedText(lines)


def cmd_config_clean() -> None:
    """Launch the full-screen cleanup confirmation panel."""
    panel = _CleanPanel()
    kb = KeyBindings()

    @kb.add("y")
    def on_yes(_event: KeyPressEvent) -> None:
        if not panel.done and panel.items:
            panel.confirm()

    @kb.add("q")
    @kb.add("escape")
    def on_quit(event: KeyPressEvent) -> None:
        event.app.exit()

    @kb.add("n")
    def on_no(event: KeyPressEvent) -> None:
        if not panel.done:
            event.app.exit()

    @kb.add("<any>")
    def on_any(_event: KeyPressEvent) -> None:
        pass

    control = FormattedTextControl(panel.render)
    window = Window(content=control, wrap_lines=True)
    layout = Layout(HSplit([window]))

    app: Application[None] = Application(
        layout=layout,
        key_bindings=kb,
        full_screen=True,
    )
    app.run()
