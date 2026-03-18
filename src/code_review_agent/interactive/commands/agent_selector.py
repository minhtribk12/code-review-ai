"""Standalone agent selector: full-screen multi-select launched via Ctrl+A."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout

from code_review_agent.agents import AGENT_REGISTRY
from code_review_agent.theme import theme

if TYPE_CHECKING:
    from prompt_toolkit.key_binding import KeyPressEvent

    from code_review_agent.interactive.session import SessionState


class _AgentSelector:
    """Multi-select agent picker with checkbox UI."""

    def __init__(self, session: SessionState) -> None:
        self.session = session
        self.choices: list[str] = [*AGENT_REGISTRY.keys(), "all"]
        self.checked: list[bool] = []
        self.cursor: int = 0
        self.error_message: str = ""
        self.is_confirmed: bool = False

        current_csv = session.config_overrides.get("default_agents", "")
        selected = {s.strip() for s in current_csv.split(",") if s.strip()}

        for choice in self.choices:
            if choice == "all":
                real_agents = [c for c in self.choices if c != "all"]
                self.checked.append(all(a in selected for a in real_agents))
            else:
                self.checked.append(choice in selected)

    def _max_concurrent(self) -> int:
        """Resolve max_concurrent_agents from overrides or base settings."""
        max_agents = getattr(self.session.settings, "max_concurrent_agents", 4)
        override = self.session.config_overrides.get("max_concurrent_agents")
        if override is not None:
            with contextlib.suppress(ValueError):
                max_agents = int(override)
        return max_agents

    def toggle(self) -> None:
        """Toggle the checkbox at the current cursor."""
        choice = self.choices[self.cursor]

        if choice == "all":
            all_checked = all(self.checked[i] for i, c in enumerate(self.choices) if c != "all")
            new_state = not all_checked
            for i in range(len(self.choices)):
                self.checked[i] = new_state
        else:
            self.checked[self.cursor] = not self.checked[self.cursor]
            # Sync "all" checkbox
            all_idx = None
            for i, c in enumerate(self.choices):
                if c == "all":
                    all_idx = i
                    break
            if all_idx is not None:
                self.checked[all_idx] = all(
                    self.checked[i] for i, c in enumerate(self.choices) if c != "all"
                )

    def confirm(self) -> bool:
        """Validate and mark confirmed. Returns True on success."""
        selected = [c for i, c in enumerate(self.choices) if self.checked[i] and c != "all"]

        max_agents = self._max_concurrent()
        if selected and len(selected) > max_agents:
            self.error_message = (
                f"Selected {len(selected)} agents but max_concurrent_agents is {max_agents}"
            )
            return False

        self.is_confirmed = True
        return True

    def selected_csv(self) -> str:
        """Return the selected agents as a comma-separated string."""
        return ",".join(c for i, c in enumerate(self.choices) if self.checked[i] and c != "all")

    def render(self) -> FormattedText:
        """Render the selector UI."""
        lines: list[tuple[str, str]] = []

        lines.append(("bold", " Select Agents\n"))
        lines.append(("dim", "  Enter/Space: toggle, Tab: confirm, Esc: cancel\n"))
        lines.append(("", "\n"))

        for i, choice in enumerate(self.choices):
            is_cursor = i == self.cursor

            if is_cursor:
                lines.append(("bold cyan", " > "))
            else:
                lines.append(("", "   "))

            checked = self.checked[i] if i < len(self.checked) else False
            if checked:
                lines.append(("green bold", "[x] "))
            else:
                lines.append(("", "[ ] "))

            style = "bold" if is_cursor else ""
            if choice == "all":
                lines.append((style, "all (select/deselect all)"))
            else:
                lines.append((style, choice))
            lines.append(("", "\n"))

        # Selected count
        checked_count = sum(
            1 for i, c in enumerate(self.choices) if self.checked[i] and c != "all"
        )
        lines.append(("", "\n"))
        lines.append(("dim", f"  {checked_count} selected"))
        lines.append(("dim", f" (max: {self._max_concurrent()})"))
        lines.append(("", "\n"))

        if self.error_message:
            lines.append(("", "\n"))
            lines.append(("bg:ansired fg:ansiwhite bold", f"  {self.error_message}  "))
            lines.append(("", "\n"))

        return FormattedText(lines)


def run_agent_selector(session: SessionState) -> None:
    """Launch the full-screen agent selector and persist the choice.

    Updates ``session.config_overrides["default_agents"]`` and saves to
    the database so the selection persists across restarts.
    """
    selector = _AgentSelector(session)

    kb = KeyBindings()

    @kb.add("up")
    def on_up(event: KeyPressEvent) -> None:
        selector.cursor = max(0, selector.cursor - 1)

    @kb.add("down")
    def on_down(event: KeyPressEvent) -> None:
        selector.cursor = min(len(selector.choices) - 1, selector.cursor + 1)

    @kb.add("enter")
    @kb.add("space")
    def on_toggle(event: KeyPressEvent) -> None:
        selector.toggle()

    @kb.add("tab")
    def on_confirm(event: KeyPressEvent) -> None:
        if selector.confirm():
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

    csv_value = selector.selected_csv()
    if csv_value:
        session.config_overrides["default_agents"] = csv_value
    elif "default_agents" in session.config_overrides:
        del session.config_overrides["default_agents"]

    session.invalidate_settings_cache()

    # Persist to database
    from code_review_agent.interactive.commands.config_cmd import save_config_to_db

    save_config_to_db(session)

    from rich.console import Console

    con = Console()
    if csv_value:
        display = csv_value.replace(",", ", ")
        con.print(f"  [{theme.success}]Agents: {display}[/{theme.success}] (saved)")
    else:
        con.print(f"  [{theme.success}]Agents reset to tier defaults[/{theme.success}] (saved)")
