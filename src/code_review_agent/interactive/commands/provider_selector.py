"""Standalone provider selector: full-screen single-select launched via Ctrl+P."""

from __future__ import annotations

from typing import TYPE_CHECKING

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout

from code_review_agent.config import KnownProvider
from code_review_agent.theme import theme

if TYPE_CHECKING:
    from prompt_toolkit.key_binding import KeyPressEvent

    from code_review_agent.interactive.session import SessionState


class _ProviderSelector:
    """Single-select provider picker with radio button UI."""

    def __init__(self, session: SessionState) -> None:
        self.session = session
        self.choices: list[str] = [p.value for p in KnownProvider]
        self.cursor: int = 0
        self.is_confirmed: bool = False

        # Determine current provider from overrides or base settings
        current = session.config_overrides.get(
            "llm_provider",
            str(getattr(session.settings, "llm_provider", "openrouter")),
        )
        for i, choice in enumerate(self.choices):
            if choice == current:
                self.cursor = i
                break

    def confirm(self) -> None:
        """Mark the selection as confirmed."""
        self.is_confirmed = True

    def selected_value(self) -> str:
        """Return the selected provider value."""
        return self.choices[self.cursor]

    def render(self) -> FormattedText:
        """Render the selector UI."""
        lines: list[tuple[str, str]] = []

        lines.append(("bold", " Select LLM Provider\n"))
        lines.append(("dim", "  Up/Down: navigate, Enter: select, Esc: cancel\n"))
        lines.append(("", "\n"))

        current = self.session.config_overrides.get(
            "llm_provider",
            str(getattr(self.session.settings, "llm_provider", "")),
        )

        for i, choice in enumerate(self.choices):
            is_cursor = i == self.cursor

            if is_cursor:
                lines.append(("bold cyan", " > "))
            else:
                lines.append(("", "   "))

            if choice == current:
                lines.append(("green bold", "(*) "))
            else:
                lines.append(("", "( ) "))

            style = "bold" if is_cursor else ""
            lines.append((style, choice))

            if choice == current:
                lines.append(("green", "  (current)"))

            lines.append(("", "\n"))

        return FormattedText(lines)


def run_provider_selector(session: SessionState) -> None:
    """Launch the full-screen provider selector and persist the choice.

    Updates ``session.config_overrides["llm_provider"]`` and saves to
    the database so the selection persists across restarts.
    """
    selector = _ProviderSelector(session)

    kb = KeyBindings()

    @kb.add("up")
    def on_up(event: KeyPressEvent) -> None:
        selector.cursor = max(0, selector.cursor - 1)

    @kb.add("down")
    def on_down(event: KeyPressEvent) -> None:
        selector.cursor = min(len(selector.choices) - 1, selector.cursor + 1)

    @kb.add("enter")
    @kb.add("space")
    def on_select(event: KeyPressEvent) -> None:
        selector.confirm()
        event.app.exit()

    @kb.add("escape")
    def on_cancel(event: KeyPressEvent) -> None:
        event.app.exit()

    control = FormattedTextControl(selector.render)
    window = Window(content=control, wrap_lines=True)
    layout = Layout(HSplit([window]))

    app: Application[None] = Application(
        layout=layout,
        key_bindings=kb,
        full_screen=True,
        refresh_interval=0.1,
    )
    app.run()

    if not selector.is_confirmed:
        return

    value = selector.selected_value()
    session.config_overrides["llm_provider"] = value
    session.invalidate_settings_cache()

    from code_review_agent.interactive.commands.config_cmd import save_config_to_db

    save_config_to_db(session)

    from rich.console import Console

    con = Console()
    con.print(f"  [{theme.success}]Provider: {value}[/{theme.success}] (saved)")
