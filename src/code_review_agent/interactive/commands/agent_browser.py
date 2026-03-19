"""Full-screen agent browser: view, edit, create, and delete agents."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import TYPE_CHECKING

import structlog
from prompt_toolkit import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout

from code_review_agent.agent_definition import AgentDefinition, AgentSource, resolve_all_agents
from code_review_agent.theme import theme

if TYPE_CHECKING:
    from prompt_toolkit.key_binding import KeyPressEvent

    from code_review_agent.interactive.session import SessionState
    from code_review_agent.storage import ReviewStorage

logger = structlog.get_logger(__name__)

_Lines = list[tuple[str, str]]

_VALID_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

_ADD_STEPS = ["name", "description", "system_prompt", "priority", "file_patterns"]


class _Mode(StrEnum):
    NAVIGATE = "navigate"
    VIEW_DETAIL = "view_detail"
    FIELD_SELECT = "field_select"
    EDIT_FIELD = "edit_field"
    EDIT_MULTILINE = "edit_multiline"
    CONFIRM_DELETE = "confirm_delete"
    ADD_INPUT = "add_input"


# Fields available for editing (label, key, is_multiline).
_EDITABLE_FIELDS: list[tuple[str, str, bool]] = [
    ("System Prompt", "system_prompt", True),
    ("Description", "description", False),
    ("Priority", "priority", False),
    ("Enabled", "enabled", False),
    ("File Patterns", "file_patterns", False),
    ("Reset to default", "_reset", False),
]


class MultilineBuffer:
    """Buffer for editing multi-line text with cursor tracking."""

    def __init__(self, text: str) -> None:
        lines = text.split("\n") if text else [""]
        self.lines: list[str] = lines
        self.row: int = len(lines) - 1
        self.col: int = len(lines[-1])
        self.scroll_offset: int = 0

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def _clamp_col(self) -> None:
        self.col = min(self.col, len(self.lines[self.row]))

    def move_up(self) -> None:
        if self.row > 0:
            self.row -= 1
            self._clamp_col()

    def move_down(self) -> None:
        if self.row < len(self.lines) - 1:
            self.row += 1
            self._clamp_col()

    def move_left(self) -> None:
        if self.col > 0:
            self.col -= 1
        elif self.row > 0:
            self.row -= 1
            self.col = len(self.lines[self.row])

    def move_right(self) -> None:
        if self.col < len(self.lines[self.row]):
            self.col += 1
        elif self.row < len(self.lines) - 1:
            self.row += 1
            self.col = 0

    def move_word_left(self) -> None:
        """Move cursor to the start of the previous word."""
        line = self.lines[self.row]
        if self.col == 0:
            self.move_left()
            return
        pos = self.col - 1
        # Skip whitespace
        while pos > 0 and not line[pos].isalnum():
            pos -= 1
        # Skip word chars
        while pos > 0 and line[pos - 1].isalnum():
            pos -= 1
        self.col = pos

    def move_word_right(self) -> None:
        """Move cursor to the end of the next word."""
        line = self.lines[self.row]
        if self.col >= len(line):
            self.move_right()
            return
        pos = self.col
        # Skip current word chars
        while pos < len(line) and line[pos].isalnum():
            pos += 1
        # Skip whitespace
        while pos < len(line) and not line[pos].isalnum():
            pos += 1
        self.col = pos

    def move_home(self) -> None:
        self.col = 0

    def move_end(self) -> None:
        self.col = len(self.lines[self.row])

    def insert(self, text: str) -> None:
        """Insert text at cursor, handling newlines from paste."""
        parts = text.split("\n")
        line = self.lines[self.row]
        before = line[: self.col]
        after = line[self.col :]

        if len(parts) == 1:
            self.lines[self.row] = before + parts[0] + after
            self.col += len(parts[0])
        else:
            self.lines[self.row] = before + parts[0]
            for i, part in enumerate(parts[1:-1], 1):
                self.lines.insert(self.row + i, part)
            last = parts[-1] + after
            self.lines.insert(self.row + len(parts) - 1, last)
            self.row += len(parts) - 1
            self.col = len(parts[-1])

    def insert_newline(self) -> None:
        line = self.lines[self.row]
        self.lines[self.row] = line[: self.col]
        self.lines.insert(self.row + 1, line[self.col :])
        self.row += 1
        self.col = 0

    def backspace(self) -> None:
        if self.col > 0:
            line = self.lines[self.row]
            self.lines[self.row] = line[: self.col - 1] + line[self.col :]
            self.col -= 1
        elif self.row > 0:
            # Join with previous line
            prev_len = len(self.lines[self.row - 1])
            self.lines[self.row - 1] += self.lines[self.row]
            del self.lines[self.row]
            self.row -= 1
            self.col = prev_len

    def delete(self) -> None:
        line = self.lines[self.row]
        if self.col < len(line):
            self.lines[self.row] = line[: self.col] + line[self.col + 1 :]
        elif self.row < len(self.lines) - 1:
            # Join with next line
            self.lines[self.row] += self.lines[self.row + 1]
            del self.lines[self.row + 1]

    def kill_to_end(self) -> None:
        """Delete from cursor to end of line (Ctrl+K)."""
        self.lines[self.row] = self.lines[self.row][: self.col]

    def kill_to_start(self) -> None:
        """Delete from start of line to cursor (Ctrl+U)."""
        self.lines[self.row] = self.lines[self.row][self.col :]
        self.col = 0

    def ensure_scroll_visible(self, viewport_height: int) -> None:
        """Adjust scroll offset to keep cursor visible."""
        if self.row < self.scroll_offset:
            self.scroll_offset = self.row
        elif self.row >= self.scroll_offset + viewport_height:
            self.scroll_offset = self.row - viewport_height + 1


class AgentBrowser:
    """State for the full-screen agent browser."""

    def __init__(self, session: SessionState) -> None:
        self.session = session
        self.agents: list[AgentDefinition] = []
        self.cursor: int = 0
        self.mode: _Mode = _Mode.NAVIGATE
        self.status_message: str = ""

        # Single-line edit state
        self.edit_field_name: str = ""
        self.edit_buffer: str = ""
        self.edit_cursor_pos: int = 0

        # Multi-line edit state
        self.ml_buffer: MultilineBuffer | None = None

        # Field selector state
        self.field_choices: list[tuple[str, str, bool]] = []
        self.field_cursor: int = 0

        # Add wizard state
        self.add_steps: list[str] = []
        self.add_step_index: int = 0
        self.add_data: dict[str, str] = {}
        self.add_error: str = ""

        self._rebuild()

    # -- Helpers ---------------------------------------------------------------

    def _rebuild(self) -> None:
        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(self.session.effective_settings.history_db_path)
        self.agents = resolve_all_agents(self.session.effective_settings, storage)
        if self.cursor >= len(self.agents):
            self.cursor = max(0, len(self.agents) - 1)

    def _current_agent(self) -> AgentDefinition | None:
        if 0 <= self.cursor < len(self.agents):
            return self.agents[self.cursor]
        return None

    def _storage(self) -> ReviewStorage:
        from code_review_agent.storage import ReviewStorage

        return ReviewStorage(self.session.effective_settings.history_db_path)

    def _reload_registry(self) -> None:
        from code_review_agent.agents import reload_agents

        reload_agents(self.session.effective_settings)

    # -- Navigation ------------------------------------------------------------

    def move_up(self) -> None:
        if self.mode == _Mode.NAVIGATE:
            self.cursor = max(0, self.cursor - 1)
        elif self.mode == _Mode.FIELD_SELECT:
            self.field_cursor = max(0, self.field_cursor - 1)

    def move_down(self) -> None:
        if self.mode == _Mode.NAVIGATE:
            self.cursor = min(len(self.agents) - 1, self.cursor + 1)
        elif self.mode == _Mode.FIELD_SELECT:
            self.field_cursor = min(len(self.field_choices) - 1, self.field_cursor + 1)

    # -- View detail -----------------------------------------------------------

    def view_detail(self) -> None:
        if self._current_agent() is not None:
            self.mode = _Mode.VIEW_DETAIL

    # -- Edit ------------------------------------------------------------------

    def start_edit(self) -> None:
        """Open field selector for the current agent."""
        agent = self._current_agent()
        if agent is None:
            return

        self.field_choices = []
        for label, key, is_multi in _EDITABLE_FIELDS:
            if key == "_reset" and agent.source != AgentSource.DB:
                continue
            self.field_choices.append((label, key, is_multi))
        self.field_cursor = 0
        self.mode = _Mode.FIELD_SELECT

    def select_field(self) -> None:
        """Confirm field selection and open the appropriate editor."""
        if not self.field_choices:
            return
        _label, key, is_multi = self.field_choices[self.field_cursor]
        agent = self._current_agent()
        if agent is None:
            return

        if key == "_reset":
            self._reset_agent(agent)
            return

        if key == "enabled":
            self._toggle_enabled(agent)
            return

        current = self._get_field_value(agent, key)
        self.edit_field_name = key

        if is_multi:
            self.ml_buffer = MultilineBuffer(current)
            self.mode = _Mode.EDIT_MULTILINE
        else:
            self.edit_buffer = current
            self.edit_cursor_pos = len(self.edit_buffer)
            self.mode = _Mode.EDIT_FIELD

    def _get_field_value(self, agent: AgentDefinition, key: str) -> str:
        if key == "file_patterns":
            return ",".join(agent.file_patterns) if agent.file_patterns else ""
        return str(getattr(agent, key, ""))

    def _toggle_enabled(self, agent: AgentDefinition) -> None:
        new_enabled = not agent.enabled
        self._save_agent_field(agent, "enabled", new_enabled)
        label = "Enabled" if new_enabled else "Disabled"
        self.status_message = f"{label} agent '{agent.name}' (saved to database)"
        self.mode = _Mode.NAVIGATE

    def _reset_agent(self, agent: AgentDefinition) -> None:
        if agent.source != AgentSource.DB:
            self.status_message = "Only DB agents can be reset"
            self.mode = _Mode.NAVIGATE
            return
        self._storage().delete_agent(agent.name)
        self._reload_registry()
        self._rebuild()
        self.status_message = f"Reset '{agent.name}' to default (DB override removed)"
        self.mode = _Mode.NAVIGATE

    def confirm_edit(self) -> None:
        """Save single-line edit."""
        agent = self._current_agent()
        if agent is None:
            self.mode = _Mode.NAVIGATE
            return

        raw = self.edit_buffer.strip()
        err = self._validate_field(self.edit_field_name, raw)
        if err:
            self.status_message = f"! {err}"
            return

        value = self._coerce_field(self.edit_field_name, raw)
        self._save_agent_field(agent, self.edit_field_name, value)
        self.status_message = f"Updated '{agent.name}' {self.edit_field_name} (saved to database)"
        self.mode = _Mode.NAVIGATE

    def confirm_multiline(self) -> None:
        """Save multi-line edit."""
        agent = self._current_agent()
        if agent is None or self.ml_buffer is None:
            self.mode = _Mode.NAVIGATE
            return

        text = self.ml_buffer.text.strip()
        if not text:
            self.status_message = "! System prompt cannot be empty"
            return

        self._save_agent_field(agent, self.edit_field_name, text)
        self.status_message = f"Updated '{agent.name}' {self.edit_field_name} (saved to database)"
        self.ml_buffer = None
        self.mode = _Mode.NAVIGATE

    def _validate_field(self, key: str, raw: str) -> str:
        if key == "priority":
            try:
                val = int(raw)
            except ValueError:
                return "Must be an integer"
            if val < 0:
                return "Must be >= 0"
        if key == "system_prompt" and not raw:
            return "System prompt cannot be empty"
        return ""

    def _coerce_field(self, key: str, raw: str) -> object:
        if key == "priority":
            return int(raw)
        if key == "file_patterns":
            if not raw:
                return None
            return [p.strip() for p in raw.split(",") if p.strip()]
        return raw

    def _save_agent_field(self, agent: AgentDefinition, key: str, value: object) -> None:
        """Save a single field edit by writing the full agent to DB."""
        storage = self._storage()

        # Load existing DB agent or create from current definition
        existing = storage.load_agent(agent.name)
        if existing is not None:
            data = existing
        else:
            data = {
                "name": agent.name,
                "system_prompt": agent.system_prompt,
                "description": agent.description,
                "priority": agent.priority,
                "enabled": agent.enabled,
                "file_patterns": agent.file_patterns,
            }

        data[key] = value

        storage.save_agent(
            name=data["name"],
            system_prompt=data["system_prompt"],
            description=data.get("description", ""),
            priority=data.get("priority", 100),
            enabled=data.get("enabled", True),
            file_patterns=data.get("file_patterns"),
        )
        self._reload_registry()
        self._rebuild()

    # -- Delete ----------------------------------------------------------------

    def request_delete(self) -> None:
        agent = self._current_agent()
        if agent is None:
            return
        if agent.source != AgentSource.DB:
            self.status_message = "Cannot delete built-in/YAML agents (use edit to override)"
            return
        self.mode = _Mode.CONFIRM_DELETE

    def confirm_delete(self) -> None:
        agent = self._current_agent()
        if agent is None:
            self.mode = _Mode.NAVIGATE
            return
        self._storage().delete_agent(agent.name)
        self._reload_registry()
        self._rebuild()
        self.status_message = f"Deleted agent '{agent.name}' from database"
        self.mode = _Mode.NAVIGATE

    # -- Add -------------------------------------------------------------------

    def start_add(self) -> None:
        self.add_steps = list(_ADD_STEPS)
        self.add_step_index = 0
        self.add_data = {}
        self.add_error = ""
        self.edit_buffer = ""
        self.edit_cursor_pos = 0
        self.ml_buffer = None
        self.mode = _Mode.ADD_INPUT

    def add_next_step(self) -> None:
        """Validate current input and advance to next step or save."""
        step = self.add_steps[self.add_step_index]
        self.add_error = ""

        if step == "system_prompt" and self.ml_buffer is not None:
            value = self.ml_buffer.text.strip()
        else:
            value = self.edit_buffer.strip()

        err = self._validate_add_step(step, value)
        if err:
            self.add_error = err
            return

        self.add_data[step] = value
        self.add_step_index += 1

        if self.add_step_index >= len(self.add_steps):
            self._save_add()
            return

        # Prepare next step
        next_step = self.add_steps[self.add_step_index]
        if next_step == "system_prompt":
            self.ml_buffer = MultilineBuffer(self._add_step_default(next_step))
        else:
            self.ml_buffer = None
            self.edit_buffer = self._add_step_default(next_step)
            self.edit_cursor_pos = len(self.edit_buffer)

    def _validate_add_step(self, step: str, value: str) -> str:
        if step == "name":
            if not value:
                return "Agent name is required"
            if not _VALID_NAME.match(value):
                return "Must be lowercase alphanumeric with underscores"
            # Check all agents (registry + DB) for name collision
            existing_names = {a.name for a in self.agents}
            if value in existing_names:
                return f"Agent '{value}' already exists"
        if step == "system_prompt" and not value:
            return "System prompt is required"
        if step == "priority" and value:
            try:
                val = int(value)
                if val < 0:
                    return "Must be >= 0"
            except ValueError:
                return "Must be an integer"
        return ""

    def _add_step_default(self, step: str) -> str:
        if step == "priority":
            return "100"
        return ""

    def _add_step_label(self, step: str) -> str:
        labels = {
            "name": "Agent name (lowercase, underscores ok)",
            "description": "Description (optional)",
            "system_prompt": "System prompt",
            "priority": "Priority (0=highest, default 100)",
            "file_patterns": "File patterns (comma-separated globs, optional)",
        }
        return labels.get(step, step)

    def _is_multiline_step(self) -> bool:
        if self.add_step_index >= len(self.add_steps):
            return False
        return self.add_steps[self.add_step_index] == "system_prompt"

    def _save_add(self) -> None:
        d = self.add_data
        priority = int(d.get("priority", "100")) if d.get("priority") else 100
        patterns_raw = d.get("file_patterns", "")
        file_patterns = (
            [p.strip() for p in patterns_raw.split(",") if p.strip()] if patterns_raw else None
        )

        self._storage().save_agent(
            name=d["name"],
            system_prompt=d["system_prompt"],
            description=d.get("description", ""),
            priority=priority,
            file_patterns=file_patterns,
        )
        self._reload_registry()
        self._rebuild()
        self.status_message = f"Created agent '{d['name']}' (saved to database)"
        self.mode = _Mode.NAVIGATE
        self.ml_buffer = None

    # -- Cancel ----------------------------------------------------------------

    def cancel_action(self) -> None:
        self.mode = _Mode.NAVIGATE
        self.edit_field_name = ""
        self.ml_buffer = None
        self.add_error = ""

    # -- Render ----------------------------------------------------------------

    def render(self) -> FormattedText:
        lines: _Lines = []

        lines.append(("bold", " Agent Browser"))
        lines.append(("", "  ("))
        lines.append(("cyan", "Enter"))
        lines.append(("", " view, "))
        lines.append(("cyan", "i"))
        lines.append(("", " edit, "))
        lines.append(("cyan", "a"))
        lines.append(("", " add, "))
        lines.append(("cyan", "d"))
        lines.append(("", " delete, "))
        lines.append(("cyan", "q"))
        lines.append(("", " quit)\n"))

        if self.status_message:
            is_err = self.status_message.startswith("!")
            style = theme.error if is_err else theme.success
            lines.append((style, f"  {self.status_message}\n"))
            self.status_message = ""
        lines.append(("", "\n"))

        if self.mode == _Mode.VIEW_DETAIL:
            return self._render_detail(lines)
        if self.mode == _Mode.FIELD_SELECT:
            return self._render_field_select(lines)
        if self.mode == _Mode.EDIT_FIELD:
            return self._render_edit(lines)
        if self.mode == _Mode.EDIT_MULTILINE:
            return self._render_multiline(lines)
        if self.mode == _Mode.CONFIRM_DELETE:
            return self._render_confirm(lines)
        if self.mode == _Mode.ADD_INPUT:
            return self._render_add(lines)

        return self._render_list(lines)

    def _render_list(self, lines: _Lines) -> FormattedText:
        if not self.agents:
            lines.append(("dim", "  No agents registered.\n"))
            return FormattedText(lines)

        visible_start = max(0, self.cursor - 15)
        visible_end = min(len(self.agents), visible_start + 30)

        for i in range(visible_start, visible_end):
            agent = self.agents[i]
            is_sel = i == self.cursor

            prefix = " > " if is_sel else "   "
            lines.append(("bold cyan" if is_sel else "", prefix))

            style = "bold" if is_sel else ""
            name_style = "dim" if not agent.enabled else style
            lines.append((name_style, f"{agent.name:<20}"))

            source_style = "green" if agent.source == AgentSource.DB else "dim"
            lines.append((source_style, f"[{agent.source}]"))

            lines.append(("dim", f"  pri={agent.priority:<4}"))

            desc = agent.description or f"Specialized {agent.name} reviewer"
            if len(desc) > 50:
                desc = desc[:47] + "..."
            lines.append(("dim" if not is_sel else "", f"  {desc}"))

            if not agent.enabled:
                lines.append(("red", " [disabled]"))

            lines.append(("", "\n"))

        lines.append(("", "\n"))
        lines.append(("dim", f"  {len(self.agents)} agents"))
        lines.append(("", "\n"))

        return FormattedText(lines)

    def _render_detail(self, lines: _Lines) -> FormattedText:
        agent = self._current_agent()
        if agent is None:
            return FormattedText(lines)

        lines.append(("bold", f"\n  Agent: {agent.name}\n\n"))

        lines.append(("bold", "  Source:        "))
        lines.append(("", f"{agent.source}\n"))
        lines.append(("bold", "  Priority:      "))
        lines.append(("", f"{agent.priority}\n"))
        lines.append(("bold", "  Enabled:       "))
        lines.append(("green" if agent.enabled else "red", f"{agent.enabled}\n"))
        lines.append(("bold", "  Description:   "))
        lines.append(("", f"{agent.description or '(none)'}\n"))

        if agent.file_patterns:
            lines.append(("bold", "  File Patterns: "))
            lines.append(("", f"{', '.join(agent.file_patterns)}\n"))

        lines.append(("bold", "\n  System Prompt:\n"))
        prompt_lines = agent.system_prompt.split("\n")
        for pl in prompt_lines[:30]:
            lines.append(("", f"    {pl}\n"))
        if len(prompt_lines) > 30:
            lines.append(("dim", f"    ... ({len(prompt_lines) - 30} more lines)\n"))

        lines.append(("", "\n"))
        lines.append(("dim", "  Esc: back, i: edit\n"))

        return FormattedText(lines)

    def _render_field_select(self, lines: _Lines) -> FormattedText:
        agent = self._current_agent()
        if agent is None:
            return FormattedText(lines)

        lines.append(("bold", f"\n  Edit: {agent.name}\n"))
        lines.append(("dim", "  Select a field to edit:\n\n"))

        for i, (label, key, _is_multi) in enumerate(self.field_choices):
            is_sel = i == self.field_cursor
            prefix = " > " if is_sel else "   "
            style = "bold cyan" if is_sel else ""
            lines.append((style, prefix))
            lines.append((style, f"{label:<20}"))

            if key == "_reset":
                lines.append(("red", "  (delete DB override)"))
            elif key == "enabled":
                is_enabled = agent.enabled
                lines.append(("green" if is_enabled else "red", f"  {is_enabled}"))
            elif key == "system_prompt":
                prompt = agent.system_prompt
                preview = prompt[:60].replace("\n", " ")
                if len(prompt) > 60:
                    preview += "..."
                lines.append(("dim", f"  {preview}"))
            else:
                field_val = self._get_field_value(agent, key)
                lines.append(("dim", f"  {field_val or '(empty)'}"))
            lines.append(("", "\n"))

        lines.append(("", "\n  "))
        lines.append(("cyan", "Enter"))
        lines.append(("", " select, "))
        lines.append(("cyan", "Esc"))
        lines.append(("", " cancel\n"))

        return FormattedText(lines)

    def _render_edit(self, lines: _Lines) -> FormattedText:
        agent = self._current_agent()
        label = agent.name if agent else "?"

        lines.append(("bold", f"\n  Edit: {label} -> {self.edit_field_name}\n\n"))
        lines.append(("", "  "))
        _render_text_input(lines, self.edit_buffer, self.edit_cursor_pos)
        lines.append(("", "\n\n"))
        lines.append(("dim", "  Enter to save, Esc to cancel\n"))

        return FormattedText(lines)

    def _render_multiline(self, lines: _Lines) -> FormattedText:
        agent = self._current_agent()
        label = agent.name if agent else "?"
        buf = self.ml_buffer
        if buf is None:
            return FormattedText(lines)

        lines.append(("bold", f"\n  Edit: {label} -> {self.edit_field_name}\n"))
        lines.append(("dim", "  Tab: save, Esc: cancel"))
        lines.append(("dim", " | Ctrl+K: kill to end, Ctrl+U: kill to start\n\n"))

        viewport_height = 25
        buf.ensure_scroll_visible(viewport_height)
        start = buf.scroll_offset
        end = min(len(buf.lines), start + viewport_height)

        for i in range(start, end):
            line_text = buf.lines[i]
            is_current = i == buf.row
            line_num = f"  {i + 1:>4} "
            lines.append(("dim", line_num))

            if is_current:
                before = line_text[: buf.col]
                after = line_text[buf.col :]
                lines.append(("", before))
                cursor_char = after[0] if after else " "
                lines.append(("bg:ansicyan fg:ansiblack blink", cursor_char))
                if len(after) > 1:
                    lines.append(("", after[1:]))
            else:
                lines.append(("", line_text))

            lines.append(("", "\n"))

        lines.append(("", "\n"))
        lines.append(("dim", f"  Line {buf.row + 1}/{len(buf.lines)}, Col {buf.col + 1}"))
        lines.append(("", "\n"))

        return FormattedText(lines)

    def _render_confirm(self, lines: _Lines) -> FormattedText:
        agent = self._current_agent()
        if agent is None:
            return FormattedText(lines)

        lines.append(("bold", f"\n  Delete agent '{agent.name}'?\n\n"))
        lines.append(("", "  Press "))
        lines.append(("bold cyan", "y"))
        lines.append(("", " to confirm, "))
        lines.append(("bold cyan", "n/Esc"))
        lines.append(("", " to cancel\n"))

        return FormattedText(lines)

    def _render_add(self, lines: _Lines) -> FormattedText:
        lines.append(("bold", "\n  Create Agent\n"))

        # Show completed steps
        for idx in range(self.add_step_index):
            step = self.add_steps[idx]
            val = self.add_data.get(step, "")
            if step == "system_prompt" and val:
                display = val[:50].replace("\n", " ") + ("..." if len(val) > 50 else "")
            else:
                display = val or "(skipped)"
            label = self._add_step_label(step)
            lines.append(("dim", f"  {label}: {display}\n"))

        # Current step
        if self.add_step_index < len(self.add_steps):
            step = self.add_steps[self.add_step_index]
            label = self._add_step_label(step)
            step_num = self.add_step_index + 1
            total = len(self.add_steps)
            lines.append(("", f"\n  [{step_num}/{total}] "))
            lines.append(("bold", f"{label}:\n"))

            if self.add_error:
                lines.append(("red bold", f"  {self.add_error}\n"))

            if step == "system_prompt" and self.ml_buffer is not None:
                lines.append(("dim", "  (Tab to confirm, Esc to cancel)\n\n"))
                buf = self.ml_buffer
                viewport_height = 20
                buf.ensure_scroll_visible(viewport_height)
                start = buf.scroll_offset
                end = min(len(buf.lines), start + viewport_height)
                for i in range(start, end):
                    is_current = i == buf.row
                    line_num = f"  {i + 1:>4} "
                    lines.append(("dim", line_num))
                    line_text = buf.lines[i]
                    if is_current:
                        before = line_text[: buf.col]
                        after = line_text[buf.col :]
                        lines.append(("", before))
                        cursor_char = after[0] if after else " "
                        lines.append(("bg:ansicyan fg:ansiblack blink", cursor_char))
                        if len(after) > 1:
                            lines.append(("", after[1:]))
                    else:
                        lines.append(("", line_text))
                    lines.append(("", "\n"))
                lines.append(("", "\n"))
                lines.append(("dim", f"  Line {buf.row + 1}/{len(buf.lines)}, Col {buf.col + 1}"))
            else:
                lines.append(("", "  "))
                _render_text_input(lines, self.edit_buffer, self.edit_cursor_pos)
                lines.append(("", "\n\n"))
                lines.append(("dim", "  Enter to continue, Esc to cancel\n"))

        return FormattedText(lines)


def _render_text_input(lines: _Lines, buffer: str, cursor_pos: int) -> None:
    """Render inline text input with cursor."""
    before = buffer[:cursor_pos]
    after = buffer[cursor_pos:]
    lines.append(("bg:ansiwhite fg:ansiblack", before))
    cursor_char = after[0] if after else " "
    lines.append(("bg:ansicyan fg:ansiblack blink", cursor_char))
    if len(after) > 1:
        lines.append(("bg:ansiwhite fg:ansiblack", after[1:]))


def _insert_char(browser: AgentBrowser, text: str) -> None:
    """Insert text at cursor position in single-line edit buffer."""
    browser.edit_buffer = (
        browser.edit_buffer[: browser.edit_cursor_pos]
        + text
        + browser.edit_buffer[browser.edit_cursor_pos :]
    )
    browser.edit_cursor_pos += len(text)


def _is_text_mode(browser: AgentBrowser) -> bool:
    """True when the browser is in a single-line text input mode."""
    return browser.mode == _Mode.EDIT_FIELD or (
        browser.mode == _Mode.ADD_INPUT and not browser._is_multiline_step()
    )


def _is_multiline_mode(browser: AgentBrowser) -> bool:
    """True when the browser is in a multi-line text input mode."""
    return browser.mode == _Mode.EDIT_MULTILINE or (
        browser.mode == _Mode.ADD_INPUT and browser._is_multiline_step()
    )


# ---------------------------------------------------------------------------
# Key bindings and application
# ---------------------------------------------------------------------------


def run_agent_browser(session: SessionState) -> None:
    """Launch the full-screen agent browser."""
    browser = AgentBrowser(session)
    kb = KeyBindings()

    @kb.add("up")
    @kb.add("k")
    def on_up(_event: KeyPressEvent) -> None:
        if _is_multiline_mode(browser) and browser.ml_buffer is not None:
            browser.ml_buffer.move_up()
        elif not _is_text_mode(browser):
            browser.move_up()

    @kb.add("down")
    @kb.add("j")
    def on_down(_event: KeyPressEvent) -> None:
        if _is_multiline_mode(browser) and browser.ml_buffer is not None:
            browser.ml_buffer.move_down()
        elif not _is_text_mode(browser):
            browser.move_down()

    @kb.add("left")
    def on_left(_event: KeyPressEvent) -> None:
        if _is_multiline_mode(browser) and browser.ml_buffer is not None:
            browser.ml_buffer.move_left()
        elif _is_text_mode(browser):
            browser.edit_cursor_pos = max(0, browser.edit_cursor_pos - 1)

    @kb.add("right")
    def on_right(_event: KeyPressEvent) -> None:
        if _is_multiline_mode(browser) and browser.ml_buffer is not None:
            browser.ml_buffer.move_right()
        elif _is_text_mode(browser):
            browser.edit_cursor_pos = min(len(browser.edit_buffer), browser.edit_cursor_pos + 1)

    @kb.add("c-left")
    def on_ctrl_left(_event: KeyPressEvent) -> None:
        if _is_multiline_mode(browser) and browser.ml_buffer is not None:
            browser.ml_buffer.move_word_left()

    @kb.add("c-right")
    def on_ctrl_right(_event: KeyPressEvent) -> None:
        if _is_multiline_mode(browser) and browser.ml_buffer is not None:
            browser.ml_buffer.move_word_right()

    @kb.add("home")
    def on_home(_event: KeyPressEvent) -> None:
        if _is_multiline_mode(browser) and browser.ml_buffer is not None:
            browser.ml_buffer.move_home()
        elif _is_text_mode(browser):
            browser.edit_cursor_pos = 0

    @kb.add("end")
    def on_end(_event: KeyPressEvent) -> None:
        if _is_multiline_mode(browser) and browser.ml_buffer is not None:
            browser.ml_buffer.move_end()
        elif _is_text_mode(browser):
            browser.edit_cursor_pos = len(browser.edit_buffer)

    @kb.add("c-k")
    def on_ctrl_k(_event: KeyPressEvent) -> None:
        if _is_multiline_mode(browser) and browser.ml_buffer is not None:
            browser.ml_buffer.kill_to_end()

    @kb.add("c-u")
    def on_ctrl_u(_event: KeyPressEvent) -> None:
        if _is_multiline_mode(browser) and browser.ml_buffer is not None:
            browser.ml_buffer.kill_to_start()

    @kb.add("enter")
    def on_enter(_event: KeyPressEvent) -> None:
        if browser.mode == _Mode.NAVIGATE:
            browser.view_detail()
        elif browser.mode == _Mode.VIEW_DETAIL:
            pass
        elif browser.mode == _Mode.FIELD_SELECT:
            browser.select_field()
        elif browser.mode == _Mode.EDIT_FIELD:
            browser.confirm_edit()
        elif _is_multiline_mode(browser) and browser.ml_buffer is not None:
            browser.ml_buffer.insert_newline()
        elif browser.mode == _Mode.ADD_INPUT:
            browser.add_next_step()

    @kb.add("tab")
    def on_tab(_event: KeyPressEvent) -> None:
        if browser.mode == _Mode.EDIT_MULTILINE:
            browser.confirm_multiline()
        elif browser.mode == _Mode.ADD_INPUT and browser._is_multiline_step():
            browser.add_next_step()

    @kb.add("space")
    def on_space(_event: KeyPressEvent) -> None:
        if _is_multiline_mode(browser) and browser.ml_buffer is not None:
            browser.ml_buffer.insert(" ")
        elif _is_text_mode(browser):
            _insert_char(browser, " ")

    @kb.add("backspace")
    def on_backspace(_event: KeyPressEvent) -> None:
        if _is_multiline_mode(browser) and browser.ml_buffer is not None:
            browser.ml_buffer.backspace()
        elif _is_text_mode(browser) and browser.edit_cursor_pos > 0:
            browser.edit_buffer = (
                browser.edit_buffer[: browser.edit_cursor_pos - 1]
                + browser.edit_buffer[browser.edit_cursor_pos :]
            )
            browser.edit_cursor_pos -= 1

    @kb.add("delete")
    def on_delete_key(_event: KeyPressEvent) -> None:
        if _is_multiline_mode(browser) and browser.ml_buffer is not None:
            browser.ml_buffer.delete()
        elif _is_text_mode(browser) and browser.edit_cursor_pos < len(browser.edit_buffer):
            browser.edit_buffer = (
                browser.edit_buffer[: browser.edit_cursor_pos]
                + browser.edit_buffer[browser.edit_cursor_pos + 1 :]
            )

    @kb.add("d")
    def on_d(_event: KeyPressEvent) -> None:
        if browser.mode == _Mode.NAVIGATE:
            browser.request_delete()
        elif _is_text_mode(browser):
            _insert_char(browser, "d")
        elif _is_multiline_mode(browser) and browser.ml_buffer is not None:
            browser.ml_buffer.insert("d")

    @kb.add("i")
    def on_i(_event: KeyPressEvent) -> None:
        if browser.mode in (_Mode.NAVIGATE, _Mode.VIEW_DETAIL):
            browser.start_edit()
        elif _is_text_mode(browser):
            _insert_char(browser, "i")
        elif _is_multiline_mode(browser) and browser.ml_buffer is not None:
            browser.ml_buffer.insert("i")

    @kb.add("a")
    def on_a(_event: KeyPressEvent) -> None:
        if browser.mode == _Mode.NAVIGATE:
            browser.start_add()
        elif _is_text_mode(browser):
            _insert_char(browser, "a")
        elif _is_multiline_mode(browser) and browser.ml_buffer is not None:
            browser.ml_buffer.insert("a")

    @kb.add("y")
    def on_y(_event: KeyPressEvent) -> None:
        if browser.mode == _Mode.CONFIRM_DELETE:
            browser.confirm_delete()
        elif _is_text_mode(browser):
            _insert_char(browser, "y")
        elif _is_multiline_mode(browser) and browser.ml_buffer is not None:
            browser.ml_buffer.insert("y")

    @kb.add("n")
    def on_n(_event: KeyPressEvent) -> None:
        if browser.mode == _Mode.CONFIRM_DELETE:
            browser.cancel_action()
        elif _is_text_mode(browser):
            _insert_char(browser, "n")
        elif _is_multiline_mode(browser) and browser.ml_buffer is not None:
            browser.ml_buffer.insert("n")

    @kb.add("escape")
    def on_escape(event: KeyPressEvent) -> None:
        if browser.mode in (
            _Mode.VIEW_DETAIL,
            _Mode.FIELD_SELECT,
            _Mode.EDIT_FIELD,
            _Mode.EDIT_MULTILINE,
            _Mode.CONFIRM_DELETE,
            _Mode.ADD_INPUT,
        ):
            browser.cancel_action()
        else:
            event.app.exit()

    @kb.add("q")
    def on_quit(event: KeyPressEvent) -> None:
        if _is_text_mode(browser):
            _insert_char(browser, "q")
        elif _is_multiline_mode(browser) and browser.ml_buffer is not None:
            browser.ml_buffer.insert("q")
        elif browser.mode in (_Mode.CONFIRM_DELETE, _Mode.FIELD_SELECT, _Mode.VIEW_DETAIL):
            browser.cancel_action()
        else:
            event.app.exit()

    @kb.add("<any>")
    def on_char(event: KeyPressEvent) -> None:
        printable = "".join(c for c in event.data if c.isprintable())
        if not printable:
            return
        if _is_text_mode(browser):
            _insert_char(browser, printable)
        elif _is_multiline_mode(browser) and browser.ml_buffer is not None:
            browser.ml_buffer.insert(printable)

    control = FormattedTextControl(browser.render)
    window = Window(content=control, wrap_lines=True)
    layout = Layout(HSplit([window]))

    app: Application[None] = Application(
        layout=layout,
        key_bindings=kb,
        full_screen=True,
        refresh_interval=0.1,
    )
    app.run()
