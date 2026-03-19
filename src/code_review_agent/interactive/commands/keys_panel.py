"""Full-screen API key manager: view, edit, sync between secrets.env and .env, delete."""

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

# Column indices for left/right navigation.
_COL_SECRETS = 0
_COL_ENV = 1


class _Mode(StrEnum):
    NAVIGATE = "navigate"
    EDIT_KEY = "edit_key"
    SYNC_POPUP = "sync_popup"
    DELETE_CONFIRM = "delete_confirm"


class _KeysPanel:
    """State for the API key manager panel."""

    def __init__(self, session: SessionState) -> None:
        self.session = session
        self.providers: list[str] = sorted(_providers_mod.PROVIDER_REGISTRY.keys())
        self.cursor: int = 0
        self.col_cursor: int = _COL_SECRETS  # 0 = secrets.env, 1 = .env
        self.mode: _Mode = _Mode.NAVIGATE
        self.status_message: str = ""
        self.sync_cursor: int = 0  # 0 = secrets.env->env, 1 = env->secrets.env

        # Text edit state
        self.edit_buffer: str = ""
        self.edit_cursor_pos: int = 0

    def _current_provider(self) -> str | None:
        if 0 <= self.cursor < len(self.providers):
            return self.providers[self.cursor]
        return None

    def _secrets_key(self, provider: str) -> str:
        return self.session.load_api_key_from_secrets(provider)

    def _env_key(self, provider: str) -> str:
        """Load key from the .env file only.

        For built-in providers (nvidia, openrouter), reads from the Pydantic
        Settings model which loads .env at startup. For custom providers,
        parses the .env file directly since Settings has no field for them.

        Does NOT check os.environ -- it is polluted by secrets.env injection.
        """
        from pydantic import SecretStr

        real_key = f"{provider}_api_key"  # pragma: allowlist secret

        # Built-in providers: check Settings model fields
        raw = getattr(self.session.settings, real_key, None)
        if isinstance(raw, SecretStr):
            val = raw.get_secret_value()
            if val and val != "__placeholder__":
                return val

        # Custom providers: parse the .env file directly
        env_key = f"{provider.upper()}_API_KEY"
        env_file = _find_env_file()
        if env_file is not None:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith(f"{env_key}="):
                    return stripped.partition("=")[2].strip()
                if stripped.startswith(f"{env_key} ="):
                    return stripped.partition("=")[2].strip()

        return ""

    def _rebuild_settings(self) -> None:
        """Rebuild the Pydantic Settings model so .env column picks up changes."""
        try:
            from code_review_agent.config import Settings

            self.session.settings = Settings()
            self.session.invalidate_settings_cache()
        except Exception:  # noqa: S110
            # Settings rebuild can fail if provider validation fails mid-edit;
            # stale display is acceptable, the file write already succeeded.
            pass

    @staticmethod
    def _mask(raw: str) -> str:
        if not raw:
            return "--"
        return _mask_secret_str(raw)

    # -- Navigation -----------------------------------------------------------

    def move_up(self) -> None:
        self.status_message = ""
        if self.mode == _Mode.NAVIGATE:
            self.cursor = max(0, self.cursor - 1)
        elif self.mode == _Mode.SYNC_POPUP:
            self.sync_cursor = max(0, self.sync_cursor - 1)

    def move_down(self) -> None:
        self.status_message = ""
        if self.mode == _Mode.NAVIGATE:
            self.cursor = min(len(self.providers) - 1, self.cursor + 1)
        elif self.mode == _Mode.SYNC_POPUP:
            self.sync_cursor = min(1, self.sync_cursor + 1)

    def move_left(self) -> None:
        if self.mode == _Mode.NAVIGATE:
            self.status_message = ""
            self.col_cursor = _COL_SECRETS
        elif self.mode == _Mode.EDIT_KEY:
            self.edit_cursor_pos = max(0, self.edit_cursor_pos - 1)

    def move_right(self) -> None:
        if self.mode == _Mode.NAVIGATE:
            self.status_message = ""
            self.col_cursor = _COL_ENV
        elif self.mode == _Mode.EDIT_KEY:
            self.edit_cursor_pos = min(len(self.edit_buffer), self.edit_cursor_pos + 1)

    # -- Edit key -------------------------------------------------------------

    def start_edit(self) -> None:
        """Open inline editor for the selected column's key."""
        provider = self._current_provider()
        if provider is None:
            return
        self.status_message = ""
        self.edit_buffer = ""
        self.edit_cursor_pos = 0
        self.mode = _Mode.EDIT_KEY

    def confirm_edit(self) -> None:
        """Save the edited key to the selected column."""
        provider = self._current_provider()
        if provider is None:
            self.mode = _Mode.NAVIGATE
            return

        value = self.edit_buffer.strip()
        if not value:
            self.status_message = "Key cannot be empty"
            self.mode = _Mode.NAVIGATE
            return

        if self.col_cursor == _COL_SECRETS:
            self.session.save_api_key(provider, value)
            self.status_message = f"Saved '{provider}' key to secrets.env"
        else:
            wrote = _write_env_key(provider, value)
            if wrote:
                self.status_message = f"Saved '{provider}' key to .env"
            else:
                os.environ[f"{provider.upper()}_API_KEY"] = value
                self.status_message = f"Saved '{provider}' key to environment (no .env file found)"

        self._rebuild_settings()
        self.mode = _Mode.NAVIGATE

    def cancel_edit(self) -> None:
        self.mode = _Mode.NAVIGATE

    def insert_text(self, text: str) -> None:
        """Insert text at cursor position (handles paste of multi-char data)."""
        self.edit_buffer = (
            self.edit_buffer[: self.edit_cursor_pos]
            + text
            + self.edit_buffer[self.edit_cursor_pos :]
        )
        self.edit_cursor_pos += len(text)

    def backspace(self) -> None:
        if self.edit_cursor_pos > 0:
            self.edit_buffer = (
                self.edit_buffer[: self.edit_cursor_pos - 1]
                + self.edit_buffer[self.edit_cursor_pos :]
            )
            self.edit_cursor_pos -= 1

    def delete_char(self) -> None:
        if self.edit_cursor_pos < len(self.edit_buffer):
            self.edit_buffer = (
                self.edit_buffer[: self.edit_cursor_pos]
                + self.edit_buffer[self.edit_cursor_pos + 1 :]
            )

    # -- Sync -----------------------------------------------------------------

    def start_sync(self) -> None:
        provider = self._current_provider()
        if provider is None:
            return
        secrets = self._secrets_key(provider)
        env = self._env_key(provider)
        if not secrets and not env:
            self.status_message = f"No key found for '{provider}' in secrets.env or .env"
            return
        self.sync_cursor = 0
        self.mode = _Mode.SYNC_POPUP

    def confirm_sync(self) -> None:
        provider = self._current_provider()
        if provider is None:
            self.mode = _Mode.NAVIGATE
            return

        if self.sync_cursor == 0:
            secrets_val = self._secrets_key(provider)
            if not secrets_val:
                self.status_message = f"No secrets.env key for '{provider}' to sync"
                self.mode = _Mode.NAVIGATE
                return
            wrote = _write_env_key(provider, secrets_val)
            if wrote:
                self.status_message = f"Synced '{provider}' key: secrets.env -> .env"
            else:
                self.status_message = (
                    f"Synced '{provider}' key to environment (no .env file found to write)"
                )
                os.environ[f"{provider.upper()}_API_KEY"] = secrets_val
        else:
            env_val = self._env_key(provider)
            if not env_val:
                self.status_message = f"No .env key for '{provider}' to sync"
                self.mode = _Mode.NAVIGATE
                return
            self.session.save_api_key(provider, env_val)
            self.status_message = f"Synced '{provider}' key: .env -> secrets.env"

        self._rebuild_settings()
        self.mode = _Mode.NAVIGATE

    # -- Delete ---------------------------------------------------------------

    def start_delete(self) -> None:
        provider = self._current_provider()
        if provider is None:
            return
        target = "secrets.env" if self.col_cursor == _COL_SECRETS else ".env"
        key_val = (
            self._secrets_key(provider)
            if self.col_cursor == _COL_SECRETS
            else self._env_key(provider)
        )
        if not key_val:
            self.status_message = f"No {target} key for '{provider}' to delete"
            return
        self.mode = _Mode.DELETE_CONFIRM

    def confirm_delete(self) -> None:
        provider = self._current_provider()
        if provider is None:
            self.mode = _Mode.NAVIGATE
            return

        if self.col_cursor == _COL_SECRETS:
            self.session.delete_api_key(provider)
            self.status_message = f"Deleted '{provider}' key from secrets.env"
        else:
            deleted = _delete_env_key(provider)
            if deleted:
                self.status_message = f"Deleted '{provider}' key from .env"
            else:
                self.status_message = f"No .env file found to delete '{provider}' key"

        self._rebuild_settings()
        self.mode = _Mode.NAVIGATE

    def cancel(self) -> None:
        self.mode = _Mode.NAVIGATE

    # -- Rendering ------------------------------------------------------------

    def render(self) -> FormattedText:
        lines: list[tuple[str, str]] = []

        lines.append(("bold", " API Key Manager"))
        lines.append(("", "  ("))
        lines.append(("cyan", "Arrows"))
        lines.append(("", " navigate, "))
        lines.append(("cyan", "Enter"))
        lines.append(("", " edit, "))
        lines.append(("cyan", "s"))
        lines.append(("", " sync, "))
        lines.append(("cyan", "d"))
        lines.append(("", " delete, "))
        lines.append(("cyan", "q"))
        lines.append(("", " quit)\n"))

        if self.status_message:
            is_err = "no " in self.status_message.lower() or "empty" in self.status_message.lower()
            style = "red" if is_err else "green"
            lines.append((style, f"\n  {self.status_message}\n"))

        lines.append(("", "\n"))

        if self.mode == _Mode.EDIT_KEY:
            return self._render_edit(lines)
        if self.mode == _Mode.SYNC_POPUP:
            return self._render_sync(lines)
        if self.mode == _Mode.DELETE_CONFIRM:
            return self._render_delete(lines)
        return self._render_list(lines)

    def _render_list(self, lines: list[tuple[str, str]]) -> FormattedText:
        # Column headers -- selected column gets a cyan marker
        secrets_marker = ">" if self.col_cursor == _COL_SECRETS else " "
        env_marker = ">" if self.col_cursor == _COL_ENV else " "

        lines.append(("bold", "   "))
        lines.append(("bold", f"{'Provider':<20}"))
        lines.append(("cyan" if self.col_cursor == _COL_SECRETS else "bold", secrets_marker))
        lines.append(("bold", f"{'secrets.env':<23}"))
        lines.append(("cyan" if self.col_cursor == _COL_ENV else "bold", env_marker))
        lines.append(("bold", ".env Key\n"))
        lines.append(("dim", "   " + "\u2500" * 64 + "\n"))

        for i, provider in enumerate(self.providers):
            is_sel = i == self.cursor
            prefix = " > " if is_sel else "   "
            row_style = "bold cyan" if is_sel else ""

            secrets = self._secrets_key(provider)
            env = self._env_key(provider)

            secrets_display = self._mask(secrets)
            env_display = self._mask(env)

            # Base styles by presence
            sec_style = "green" if secrets else "dim"  # pragma: allowlist secret
            env_style = "green" if env else "dim"

            # Selected cell: bold + cyan brackets around value
            is_sec_sel = is_sel and self.col_cursor == _COL_SECRETS
            is_env_sel = is_sel and self.col_cursor == _COL_ENV

            lines.append((row_style, prefix))
            lines.append((row_style, f"{provider:<20}"))

            if is_sec_sel:
                lines.append(("bold cyan", "["))
                lines.append(("bold cyan", secrets_display))
                # Pad to align: 24 total = 1 bracket + display + pad + 1 bracket
                pad = max(0, 22 - len(secrets_display))
                lines.append(("", " " * pad))
                lines.append(("bold cyan", "]"))
            else:
                lines.append((sec_style, f" {secrets_display:<23}"))

            if is_env_sel:
                lines.append(("bold cyan", "["))
                lines.append(("bold cyan", env_display))
                lines.append(("bold cyan", "]"))
            else:
                lines.append((env_style, f" {env_display}"))

            lines.append(("", "\n"))

        lines.append(("", "\n"))
        lines.append(("dim", f"  {len(self.providers)} providers\n"))

        return FormattedText(lines)

    def _render_edit(self, lines: list[tuple[str, str]]) -> FormattedText:
        provider = self._current_provider() or "?"
        target = "secrets.env" if self.col_cursor == _COL_SECRETS else ".env"
        lines.append(("bold", f"  Edit API key for '{provider}' in {target}\n"))
        lines.append(("", "  "))
        lines.append(("cyan", "Enter"))
        lines.append(("", " save, "))
        lines.append(("cyan", "Esc"))
        lines.append(("", " cancel. Paste supported.\n\n"))
        lines.append(("", "  "))

        # Show masked key (first 4 chars visible, rest masked)
        buf = self.edit_buffer
        pos = self.edit_cursor_pos
        display = buf if len(buf) <= 4 else buf[:4] + "*" * (len(buf) - 4)

        before = display[:pos] if pos <= len(display) else display
        after = display[pos:] if pos <= len(display) else ""

        lines.append(("bg:ansiwhite fg:ansiblack", before))
        cursor_char = after[0] if after else " "
        lines.append(("bg:ansicyan fg:ansiblack blink", cursor_char))
        if len(after) > 1:
            lines.append(("bg:ansiwhite fg:ansiblack", after[1:]))

        lines.append(("", "\n"))

        if self.edit_buffer:
            lines.append(("dim", f"\n  {len(self.edit_buffer)} characters entered\n"))

        return FormattedText(lines)

    def _render_sync(self, lines: list[tuple[str, str]]) -> FormattedText:
        provider = self._current_provider() or "?"
        lines.append(("bold", f"  Sync API key for '{provider}'\n\n"))

        options = [
            "secrets.env -> .env  (overwrite .env with secrets.env value)",
            ".env -> secrets.env  (overwrite secrets.env with .env value)",
        ]
        for i, label in enumerate(options):
            is_sel = i == self.sync_cursor
            prefix = " > " if is_sel else "   "
            marker = f"[{i + 1}]"
            style = "bold cyan" if is_sel else ""
            lines.append((style, f"  {prefix}{marker} {label}\n"))

        lines.append(("", "\n"))
        lines.append(("", "  "))
        lines.append(("cyan", "Enter"))
        lines.append(("", " confirm, "))
        lines.append(("cyan", "Esc"))
        lines.append(("", " cancel\n"))

        return FormattedText(lines)

    def _render_delete(self, lines: list[tuple[str, str]]) -> FormattedText:
        provider = self._current_provider() or "?"
        target = "secrets.env" if self.col_cursor == _COL_SECRETS else ".env"
        lines.append(("bold red", f"  Delete API key for '{provider}' from {target}?\n"))
        lines.append(("", "\n"))
        lines.append(("", "  "))
        lines.append(("cyan", "y"))
        lines.append(("", " confirm, "))
        lines.append(("cyan", "n/Esc"))
        lines.append(("", " cancel\n"))

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
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines.append("\n")
        new_lines.append(f"{env_key}={value}\n")

    env_path.write_text("".join(new_lines), encoding="utf-8")

    os.environ[env_key] = value
    return True


def _delete_env_key(provider: str) -> bool:
    """Remove an API key line from the .env file.

    Returns True if the key was found and removed, False otherwise.
    """
    env_key = f"{provider.upper()}_API_KEY"
    env_path = _find_env_file()
    if env_path is None:
        return False

    lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
    new_lines = [
        line
        for line in lines
        if not line.lstrip().startswith(f"{env_key}=")
        and not line.lstrip().startswith(f"{env_key} =")
    ]
    if len(new_lines) == len(lines):
        return False

    env_path.write_text("".join(new_lines), encoding="utf-8")
    os.environ.pop(env_key, None)
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
        if panel.mode in (_Mode.NAVIGATE, _Mode.SYNC_POPUP):
            panel.move_up()

    @kb.add("down")
    @kb.add("j")
    def on_down(_event: KeyPressEvent) -> None:
        if panel.mode in (_Mode.NAVIGATE, _Mode.SYNC_POPUP):
            panel.move_down()

    @kb.add("left")
    def on_left(_event: KeyPressEvent) -> None:
        panel.move_left()

    @kb.add("right")
    def on_right(_event: KeyPressEvent) -> None:
        panel.move_right()

    @kb.add("enter")
    def on_enter(_event: KeyPressEvent) -> None:
        if panel.mode == _Mode.NAVIGATE:
            panel.start_edit()
        elif panel.mode == _Mode.EDIT_KEY:
            panel.confirm_edit()
        elif panel.mode == _Mode.SYNC_POPUP:
            panel.confirm_sync()

    @kb.add("s")
    def on_sync(_event: KeyPressEvent) -> None:
        if panel.mode == _Mode.NAVIGATE:
            panel.start_sync()
        elif panel.mode == _Mode.EDIT_KEY:
            panel.insert_text("s")

    @kb.add("d")
    def on_delete(_event: KeyPressEvent) -> None:
        if panel.mode == _Mode.NAVIGATE:
            panel.start_delete()
        elif panel.mode == _Mode.EDIT_KEY:
            panel.insert_text("d")

    @kb.add("y")
    def on_yes(_event: KeyPressEvent) -> None:
        if panel.mode == _Mode.DELETE_CONFIRM:
            panel.confirm_delete()
        elif panel.mode == _Mode.EDIT_KEY:
            panel.insert_text("y")

    @kb.add("n")
    def on_no(_event: KeyPressEvent) -> None:
        if panel.mode == _Mode.DELETE_CONFIRM:
            panel.cancel()
        elif panel.mode == _Mode.EDIT_KEY:
            panel.insert_text("n")

    @kb.add("1")
    def on_one(_event: KeyPressEvent) -> None:
        if panel.mode == _Mode.SYNC_POPUP:
            panel.sync_cursor = 0
            panel.confirm_sync()
        elif panel.mode == _Mode.EDIT_KEY:
            panel.insert_text("1")

    @kb.add("2")
    def on_two(_event: KeyPressEvent) -> None:
        if panel.mode == _Mode.SYNC_POPUP:
            panel.sync_cursor = 1
            panel.confirm_sync()
        elif panel.mode == _Mode.EDIT_KEY:
            panel.insert_text("2")

    @kb.add("backspace")
    def on_backspace(_event: KeyPressEvent) -> None:
        if panel.mode == _Mode.EDIT_KEY:
            panel.backspace()

    @kb.add("delete")
    def on_delete_char(_event: KeyPressEvent) -> None:
        if panel.mode == _Mode.EDIT_KEY:
            panel.delete_char()

    @kb.add("space")
    def on_space(_event: KeyPressEvent) -> None:
        if panel.mode == _Mode.EDIT_KEY:
            panel.insert_text(" ")

    @kb.add("escape")
    def on_escape(event: KeyPressEvent) -> None:
        if panel.mode == _Mode.EDIT_KEY:
            panel.cancel_edit()
        elif panel.mode in (_Mode.SYNC_POPUP, _Mode.DELETE_CONFIRM):
            panel.cancel()
        else:
            event.app.exit()

    @kb.add("q")
    def on_quit(event: KeyPressEvent) -> None:
        if panel.mode == _Mode.EDIT_KEY:
            panel.insert_text("q")
        elif panel.mode in (_Mode.SYNC_POPUP, _Mode.DELETE_CONFIRM):
            panel.cancel()
        else:
            event.app.exit()

    @kb.add("<any>")
    def on_char(event: KeyPressEvent) -> None:
        if panel.mode == _Mode.EDIT_KEY:
            printable = "".join(c for c in event.data if c.isprintable())
            if printable:
                panel.insert_text(printable)

    return kb
