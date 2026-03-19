"""Startup key setup panel: ensures at least one LLM provider is available."""

from __future__ import annotations

import os
import re
from enum import StrEnum
from typing import TYPE_CHECKING

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout

import code_review_agent.providers as _providers_mod
from code_review_agent.providers import get_provider
from code_review_agent.theme import theme

if TYPE_CHECKING:
    from prompt_toolkit.key_binding import KeyPressEvent

    from code_review_agent.interactive.session import SessionState

_LOCAL_URL_PATTERN = re.compile(
    r"https?://(localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0"
    r"|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+"
    r"|192\.168\.\d+\.\d+)"
)


def _is_local_provider(provider_key: str) -> bool:
    """Check if a provider uses a local/private network URL."""
    try:
        info = get_provider(provider_key)
        return bool(_LOCAL_URL_PATTERN.match(info.base_url))
    except KeyError:
        return False


def _provider_has_key(provider_key: str, session: SessionState) -> bool:
    """Check if a provider has an API key available."""
    if _is_local_provider(provider_key):
        return True

    # Check built-in settings fields
    key = session.settings.resolve_api_key_for(provider_key)
    if key is not None:
        return True

    # Check env var (may be set by DB injection or user)
    env_key = f"{provider_key.upper()}_API_KEY"
    if os.environ.get(env_key):
        return True

    # Check secrets.env
    try:
        return bool(session._get_secrets_store().load_key(provider_key))
    except Exception:
        return False


def check_providers_ready(session: SessionState) -> bool:
    """Return True if at least one provider has a key or is local."""
    return any(_provider_has_key(key, session) for key in _providers_mod.PROVIDER_REGISTRY)


def run_startup_key_setup(session: SessionState) -> bool:
    """Show interactive key setup panel. Returns True if user configured a key."""
    setup = _KeySetup(session)
    kb = _build_keybindings(setup)

    control = FormattedTextControl(setup.render)
    window = Window(content=control, wrap_lines=True)
    layout = Layout(HSplit([window]))

    app: Application[None] = Application(
        layout=layout,
        key_bindings=kb,
        full_screen=True,
        refresh_interval=0.1,
    )
    app.run()

    return setup.has_key_configured


class _Mode(StrEnum):
    NAVIGATE = "navigate"
    INPUT_KEY = "input_key"


class _KeySetup:
    """State for the startup key setup panel."""

    def __init__(self, session: SessionState) -> None:
        self.session = session
        self.providers: list[str] = sorted(_providers_mod.PROVIDER_REGISTRY.keys())
        self.cursor: int = 0
        self.mode: _Mode = _Mode.NAVIGATE
        self.has_key_configured: bool = False
        self.status_message: str = ""

        # Key input state
        self.edit_buffer: str = ""
        self.edit_cursor_pos: int = 0
        self.edit_provider: str = ""

    def move_up(self) -> None:
        self.cursor = max(0, self.cursor - 1)

    def move_down(self) -> None:
        self.cursor = min(len(self.providers) - 1, self.cursor + 1)

    def start_key_input(self) -> None:
        """Open the key input field for the selected provider."""
        if not self.providers:
            return
        provider = self.providers[self.cursor]
        if _is_local_provider(provider):
            self.status_message = f"'{provider}' is a local server, no key needed"
            return
        self.edit_provider = provider
        self.edit_buffer = ""
        self.edit_cursor_pos = 0
        self.mode = _Mode.INPUT_KEY

    def confirm_key(self) -> None:
        """Save the entered key to secrets.env and env var."""
        key_value = self.edit_buffer.strip()
        if not key_value:
            self.status_message = "Key cannot be empty"
            return

        provider = self.edit_provider

        # Save to secrets.env (also injects into os.environ)
        try:
            self.session.save_api_key(provider, key_value)
        except Exception:
            self.status_message = "Failed to save key to secrets.env"
            return

        self.has_key_configured = True
        self.status_message = f"API key saved for '{provider}'"
        self.mode = _Mode.NAVIGATE

    def cancel_input(self) -> None:
        """Cancel key input and return to navigation."""
        self.mode = _Mode.NAVIGATE

    def _is_ready(self) -> bool:
        """Return True if at least one provider is usable."""
        return check_providers_ready(self.session)

    def render(self) -> FormattedText:
        """Render the key setup panel."""
        lines: list[tuple[str, str]] = []
        is_ready = self._is_ready()

        lines.append(("bold", " LLM Provider Setup\n"))
        lines.append(("", "\n"))
        if is_ready:
            lines.append(("green", "  At least one provider is ready.\n"))
            lines.append(("", "  Press "))
            lines.append(("cyan", "c"))
            lines.append(("", " to continue, or configure more providers.\n"))
        else:
            lines.append(("red", "  No LLM provider is configured.\n"))
            lines.append(("", "  Select a provider and press "))
            lines.append(("cyan", "Enter"))
            lines.append(("", " to input your API key.\n"))

        if self.status_message:
            msg_lower = self.status_message.lower()
            is_err = "cannot" in msg_lower or "empty" in msg_lower or "failed" in msg_lower
            style = theme.error if is_err else theme.success
            lines.append((style, f"\n  {self.status_message}\n"))
            self.status_message = ""
        lines.append(("", "\n"))

        if self.mode == _Mode.INPUT_KEY:
            return self._render_key_input(lines)

        for i, provider in enumerate(self.providers):
            is_sel = i == self.cursor
            prefix = " > " if is_sel else "   "
            style = "bold cyan" if is_sel else ""
            lines.append((style, prefix))
            lines.append((style, provider))

            if _is_local_provider(provider):
                lines.append(("green", " (local server)"))
            elif _provider_has_key(provider, self.session):
                lines.append(("green", " (key set)"))
            else:
                lines.append(("red", " (no key)"))

            try:
                info = get_provider(provider)
                lines.append(("dim", f"  {info.base_url}"))
            except KeyError:
                pass

            lines.append(("", "\n"))

        lines.append(("", "\n"))
        lines.append(("", "  "))
        lines.append(("cyan", "Up/Down"))
        lines.append(("", " navigate, "))
        lines.append(("cyan", "Enter"))
        lines.append(("", " input key, "))
        lines.append(("cyan", "c"))
        lines.append(("", " continue, "))
        lines.append(("cyan", "q"))
        lines.append(("", " quit\n"))

        return FormattedText(lines)

    def _render_key_input(self, lines: list[tuple[str, str]]) -> FormattedText:
        """Render the key input field with masked display."""
        lines.append(("bold", f"  Enter API key for '{self.edit_provider}':\n"))
        lines.append(("dim", "  Paste or type your key. Enter to save, Esc to cancel.\n\n"))
        lines.append(("", "  "))

        # Show masked key (show first 4 chars, mask the rest)
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

        return FormattedText(lines)


def _build_keybindings(setup: _KeySetup) -> KeyBindings:
    """Build key bindings for the startup key setup panel."""
    kb = KeyBindings()

    @kb.add("up")
    @kb.add("k")
    def on_up(_event: KeyPressEvent) -> None:
        if setup.mode == _Mode.NAVIGATE:
            setup.move_up()

    @kb.add("down")
    @kb.add("j")
    def on_down(_event: KeyPressEvent) -> None:
        if setup.mode == _Mode.NAVIGATE:
            setup.move_down()

    @kb.add("enter")
    def on_enter(_event: KeyPressEvent) -> None:
        if setup.mode == _Mode.NAVIGATE:
            setup.start_key_input()
        elif setup.mode == _Mode.INPUT_KEY:
            setup.confirm_key()

    @kb.add("escape")
    def on_escape(_event: KeyPressEvent) -> None:
        if setup.mode == _Mode.INPUT_KEY:
            setup.cancel_input()

    @kb.add("c")
    def on_continue(event: KeyPressEvent) -> None:
        if setup.mode == _Mode.NAVIGATE:
            if setup._is_ready():
                event.app.exit()
            else:
                setup.status_message = "Configure at least one provider before continuing"
        elif setup.mode == _Mode.INPUT_KEY:
            _insert_char(setup, "c")

    @kb.add("q")
    def on_quit(event: KeyPressEvent) -> None:
        if setup.mode == _Mode.NAVIGATE:
            raise SystemExit(0)
        elif setup.mode == _Mode.INPUT_KEY:
            _insert_char(setup, "q")

    @kb.add("space")
    def on_space(_event: KeyPressEvent) -> None:
        if setup.mode == _Mode.INPUT_KEY:
            _insert_char(setup, " ")

    @kb.add("backspace")
    def on_backspace(_event: KeyPressEvent) -> None:
        if setup.mode == _Mode.INPUT_KEY and setup.edit_cursor_pos > 0:
            setup.edit_buffer = (
                setup.edit_buffer[: setup.edit_cursor_pos - 1]
                + setup.edit_buffer[setup.edit_cursor_pos :]
            )
            setup.edit_cursor_pos -= 1

    @kb.add("left")
    def on_left(_event: KeyPressEvent) -> None:
        if setup.mode == _Mode.INPUT_KEY:
            setup.edit_cursor_pos = max(0, setup.edit_cursor_pos - 1)

    @kb.add("right")
    def on_right(_event: KeyPressEvent) -> None:
        if setup.mode == _Mode.INPUT_KEY:
            setup.edit_cursor_pos = min(len(setup.edit_buffer), setup.edit_cursor_pos + 1)

    @kb.add("<any>")
    def on_char(event: KeyPressEvent) -> None:
        if setup.mode == _Mode.INPUT_KEY:
            printable = "".join(c for c in event.data if c.isprintable())
            if printable:
                _insert_char(setup, printable)

    return kb


def _insert_char(setup: _KeySetup, text: str) -> None:
    """Insert text at cursor position in the edit buffer."""
    setup.edit_buffer = (
        setup.edit_buffer[: setup.edit_cursor_pos]
        + text
        + setup.edit_buffer[setup.edit_cursor_pos :]
    )
    setup.edit_cursor_pos += len(text)
