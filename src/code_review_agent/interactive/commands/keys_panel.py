"""Full-screen API key manager: view, sync between DB and .env, delete."""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout

import code_review_agent.providers as _providers_mod
from code_review_agent.interactive.commands.config_edit import _mask_secret_str

if TYPE_CHECKING:
    from prompt_toolkit.key_binding import KeyPressEvent

    from code_review_agent.interactive.session import SessionState


class _Mode(StrEnum):
    NAVIGATE = "navigate"
    SYNC_POPUP = "sync_popup"
    DELETE_CONFIRM = "delete_confirm"


class _KeysPanel:
    """State for the API key manager panel."""

    def __init__(self, session: SessionState) -> None:
        self.session = session
        self.providers: list[str] = sorted(_providers_mod.PROVIDER_REGISTRY.keys())
        self.cursor: int = 0
        self.mode: _Mode = _Mode.NAVIGATE
        self.status_message: str = ""
        self.sync_cursor: int = 0  # 0 = DB->env, 1 = env->DB

    def _current_provider(self) -> str | None:
        if 0 <= self.cursor < len(self.providers):
            return self.providers[self.cursor]
        return None

    def _db_key(self, provider: str) -> str:
        return self.session.load_api_key_from_db(provider)

    def _env_key(self, provider: str) -> str:
        return self.session.load_api_key_from_env(provider)

    @staticmethod
    def _mask(raw: str) -> str:
        if not raw:
            return "--"
        return _mask_secret_str(raw)

    def move_up(self) -> None:
        if self.mode == _Mode.NAVIGATE:
            self.cursor = max(0, self.cursor - 1)
        elif self.mode == _Mode.SYNC_POPUP:
            self.sync_cursor = max(0, self.sync_cursor - 1)

    def move_down(self) -> None:
        if self.mode == _Mode.NAVIGATE:
            self.cursor = min(len(self.providers) - 1, self.cursor + 1)
        elif self.mode == _Mode.SYNC_POPUP:
            self.sync_cursor = min(1, self.sync_cursor + 1)

    def start_sync(self) -> None:
        provider = self._current_provider()
        if provider is None:
            return
        db = self._db_key(provider)
        env = self._env_key(provider)
        if not db and not env:
            self.status_message = f"No key found for '{provider}' in DB or .env"
            return
        self.sync_cursor = 0
        self.mode = _Mode.SYNC_POPUP

    def confirm_sync(self) -> None:
        provider = self._current_provider()
        if provider is None:
            self.mode = _Mode.NAVIGATE
            return

        if self.sync_cursor == 0:
            # DB -> .env
            db_val = self._db_key(provider)
            if not db_val:
                self.status_message = f"No DB key for '{provider}' to sync"
                self.mode = _Mode.NAVIGATE
                return
            wrote = _write_env_key(provider, db_val)
            if wrote:
                self.status_message = f"Synced '{provider}' key: DB -> .env"
            else:
                self.status_message = (
                    f"Synced '{provider}' key to environment (no .env file found to write)"
                )
                os.environ[f"{provider.upper()}_API_KEY"] = db_val
        else:
            # .env -> DB
            env_val = self._env_key(provider)
            if not env_val:
                self.status_message = f"No .env key for '{provider}' to sync"
                self.mode = _Mode.NAVIGATE
                return
            self.session.save_api_key_to_db(provider, env_val)
            self.status_message = f"Synced '{provider}' key: .env -> DB"

        self.mode = _Mode.NAVIGATE

    def start_delete(self) -> None:
        provider = self._current_provider()
        if provider is None:
            return
        db = self._db_key(provider)
        if not db:
            self.status_message = f"No DB key for '{provider}' to delete"
            return
        self.mode = _Mode.DELETE_CONFIRM

    def confirm_delete(self) -> None:
        provider = self._current_provider()
        if provider is None:
            self.mode = _Mode.NAVIGATE
            return
        self.session.delete_api_key_from_db(provider)
        self.status_message = f"Deleted '{provider}' key from database"
        self.mode = _Mode.NAVIGATE

    def cancel(self) -> None:
        self.mode = _Mode.NAVIGATE

    def render(self) -> FormattedText:
        lines: list[tuple[str, str]] = []

        lines.append(("bold", " API Key Manager\n"))
        lines.append(("dim", "  s sync  d delete  q quit\n"))

        if self.status_message:
            is_err = "no " in self.status_message.lower()
            style = "red" if is_err else "green"
            lines.append((style, f"\n  {self.status_message}\n"))
            self.status_message = ""

        lines.append(("", "\n"))

        if self.mode == _Mode.SYNC_POPUP:
            return self._render_sync(lines)
        if self.mode == _Mode.DELETE_CONFIRM:
            return self._render_delete(lines)
        return self._render_list(lines)

    def _render_list(self, lines: list[tuple[str, str]]) -> FormattedText:
        # Header
        lines.append(("bold", "   "))
        lines.append(("bold", f"{'Provider':<20}"))
        lines.append(("bold", f"{'DB Key':<24}"))
        lines.append(("bold", ".env Key\n"))
        lines.append(("dim", "   " + "\u2500" * 64 + "\n"))

        for i, provider in enumerate(self.providers):
            is_sel = i == self.cursor
            prefix = " > " if is_sel else "   "
            style = "bold cyan" if is_sel else ""

            db = self._db_key(provider)
            env = self._env_key(provider)

            db_display = self._mask(db)
            env_display = self._mask(env)

            db_style = "green" if db else "dim"
            env_style = "green" if env else "dim"

            lines.append((style, prefix))
            lines.append((style, f"{provider:<20}"))
            lines.append((db_style, f"{db_display:<24}"))
            lines.append((env_style, f"{env_display}\n"))

        lines.append(("", "\n"))
        lines.append(("dim", f"  {len(self.providers)} providers\n"))

        return FormattedText(lines)

    def _render_sync(self, lines: list[tuple[str, str]]) -> FormattedText:
        provider = self._current_provider() or "?"
        lines.append(("bold", f"  Sync API key for '{provider}'\n\n"))

        options = [
            "DB -> .env  (overwrite .env with database value)",
            ".env -> DB  (overwrite database with .env value)",
        ]
        for i, label in enumerate(options):
            is_sel = i == self.sync_cursor
            prefix = " > " if is_sel else "   "
            marker = f"[{i + 1}]"
            style = "bold cyan" if is_sel else ""
            lines.append((style, f"  {prefix}{marker} {label}\n"))

        lines.append(("", "\n"))
        lines.append(("dim", "  Enter confirm, Esc cancel\n"))

        return FormattedText(lines)

    def _render_delete(self, lines: list[tuple[str, str]]) -> FormattedText:
        provider = self._current_provider() or "?"
        lines.append(("bold red", f"  Delete API key for '{provider}' from database?\n"))
        lines.append(("", "\n"))
        lines.append(("dim", "  Press y to confirm, n/Esc to cancel\n"))

        return FormattedText(lines)


def _find_env_file() -> Path | None:
    """Find the .env file in common locations."""
    candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[4] / ".env",  # project root
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def _write_env_key(provider: str, value: str) -> bool:
    """Write or update an API key in the .env file.

    Returns True if the .env file was written, False if not found.
    """
    env_key = f"{provider.upper()}_API_KEY"
    env_path = _find_env_file()
    if env_path is None:
        return False

    lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
    found = False
    new_lines: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(f"{env_key}=") or stripped.startswith(f"{env_key} ="):
            new_lines.append(f"{env_key}={value}\n")
            found = True
        else:
            new_lines.append(line)

    if not found:
        # Append with a newline separator if file doesn't end with one
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines.append("\n")
        new_lines.append(f"{env_key}={value}\n")

    env_path.write_text("".join(new_lines), encoding="utf-8")

    # Also inject into current process env
    os.environ[env_key] = value
    return True


def run_keys_panel(session: SessionState) -> None:
    """Launch the full-screen API key manager."""
    panel = _KeysPanel(session)
    kb = _build_keybindings(panel)

    control = FormattedTextControl(panel.render)
    window = Window(content=control, wrap_lines=True)
    layout = Layout(HSplit([window]))

    app: Application[None] = Application(
        layout=layout,
        key_bindings=kb,
        full_screen=True,
        refresh_interval=0.1,
    )
    app.run()


def _build_keybindings(panel: _KeysPanel) -> KeyBindings:
    """Build key bindings for the keys panel."""
    kb = KeyBindings()

    @kb.add("up")
    @kb.add("k")
    def on_up(_event: KeyPressEvent) -> None:
        panel.move_up()

    @kb.add("down")
    @kb.add("j")
    def on_down(_event: KeyPressEvent) -> None:
        panel.move_down()

    @kb.add("s")
    def on_sync(_event: KeyPressEvent) -> None:
        if panel.mode == _Mode.NAVIGATE:
            panel.start_sync()

    @kb.add("d")
    def on_delete(_event: KeyPressEvent) -> None:
        if panel.mode == _Mode.NAVIGATE:
            panel.start_delete()

    @kb.add("y")
    def on_yes(_event: KeyPressEvent) -> None:
        if panel.mode == _Mode.DELETE_CONFIRM:
            panel.confirm_delete()

    @kb.add("n")
    def on_no(_event: KeyPressEvent) -> None:
        if panel.mode == _Mode.DELETE_CONFIRM:
            panel.cancel()

    @kb.add("1")
    def on_one(_event: KeyPressEvent) -> None:
        if panel.mode == _Mode.SYNC_POPUP:
            panel.sync_cursor = 0
            panel.confirm_sync()

    @kb.add("2")
    def on_two(_event: KeyPressEvent) -> None:
        if panel.mode == _Mode.SYNC_POPUP:
            panel.sync_cursor = 1
            panel.confirm_sync()

    @kb.add("enter")
    def on_enter(_event: KeyPressEvent) -> None:
        if panel.mode == _Mode.SYNC_POPUP:
            panel.confirm_sync()

    @kb.add("escape")
    def on_escape(event: KeyPressEvent) -> None:
        if panel.mode in (_Mode.SYNC_POPUP, _Mode.DELETE_CONFIRM):
            panel.cancel()
        else:
            event.app.exit()

    @kb.add("q")
    def on_quit(event: KeyPressEvent) -> None:
        if panel.mode in (_Mode.SYNC_POPUP, _Mode.DELETE_CONFIRM):
            panel.cancel()
        else:
            event.app.exit()

    return kb
