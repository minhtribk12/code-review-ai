"""Standalone provider selector: full-screen single-select launched via Ctrl+P."""

from __future__ import annotations

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


class _ProviderSelector:
    """Single-select provider picker with radio button UI."""

    def __init__(self, session: SessionState) -> None:
        self.session = session
        self.choices: list[str] = sorted(_providers_mod.PROVIDER_REGISTRY.keys())
        self.cursor: int = 0
        self.is_confirmed: bool = False

        # Load health status for "(not working)" labels
        from code_review_agent.interactive.repl import get_health_status

        self.health = get_health_status(session)

        # Determine current provider from overrides or base settings
        current = session.config_overrides.get(
            "llm_provider",
            str(getattr(session.settings, "llm_provider", "nvidia")),
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

            is_broken = choice in self.health.get("provider", set())
            style = "bold" if is_cursor else ""
            lines.append((style, choice))

            if is_broken:
                lines.append(("red bold", " (not working)"))
            elif choice == current:
                lines.append(("green", " (current)"))

            # Show provider info from registry
            try:
                provider_info = get_provider(choice)
                lines.append(("dim", f"  {provider_info.base_url}"))
            except KeyError:
                pass

            lines.append(("", "\n"))

        return FormattedText(lines)


def run_provider_selector(session: SessionState) -> None:
    """Launch the full-screen provider selector and persist the choice.

    Updates ``session.config_overrides["llm_provider"]`` and cascades
    changes to ``llm_model`` and ``llm_base_url``. Saves to config.yaml
    so the selection persists across restarts.
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
    old_value = session.config_overrides.get(
        "llm_provider",
        str(getattr(session.settings, "llm_provider", "")),
    )

    # Snapshot previous LLM config for revert on failure
    prev_config = {
        "llm_provider": old_value,
        "llm_model": session.config_overrides.get(
            "llm_model", str(getattr(session.settings, "llm_model", ""))
        ),
        "llm_base_url": session.config_overrides.get(
            "llm_base_url", str(getattr(session.settings, "llm_base_url", "") or "")
        ),
    }

    session.config_overrides["llm_provider"] = value

    # Cascade: update base_url and model when provider changes
    if value != old_value:
        try:
            provider_info = get_provider(value)
            session.config_overrides["llm_base_url"] = provider_info.base_url
            session.config_overrides["llm_model"] = provider_info.default_model
        except KeyError:
            pass

    session.invalidate_settings_cache()

    from code_review_agent.interactive.commands.config_cmd import save_config_to_yaml

    save_config_to_yaml(session)

    from rich.console import Console

    con = Console()
    con.print(f"  [{theme.success}]Provider: {value}[/{theme.success}] (saved)")
    if value != old_value:
        try:
            provider_info = get_provider(value)
            con.print(f"  [{theme.success}]Model: {provider_info.default_model}[/{theme.success}]")
            con.print(f"  [{theme.success}]Base URL: {provider_info.base_url}[/{theme.success}]")
        except KeyError:
            pass

        # Run connection test after provider change (revert on failure)
        if session.effective_settings.test_connection_on_start:
            from code_review_agent.interactive.repl import run_connection_test

            run_connection_test(session, previous_llm_config=prev_config)
