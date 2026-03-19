"""Interactive config editor: navigate, edit, and validate settings in the TUI."""

from __future__ import annotations

import contextlib
import types
import typing
from enum import EnumType
from typing import TYPE_CHECKING

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from pydantic import SecretStr

from code_review_agent.agents import AGENT_REGISTRY
from code_review_agent.config import Settings
from code_review_agent.interactive.config_categories import CONFIG_CATEGORIES
from code_review_agent.providers import get_provider
from code_review_agent.theme import theme

if TYPE_CHECKING:
    from prompt_toolkit.key_binding import KeyPressEvent
    from pydantic.fields import FieldInfo

    from code_review_agent.interactive.session import SessionState

# Fields to exclude from the editor (computed, internal, or replaced by virtual).
_EXCLUDED_FIELDS = frozenset(
    {
        "resolved_llm_base_url",
        "resolved_api_key",
        "resolved_default_model",
        "nvidia_api_key",
        "openrouter_api_key",
    }
)

# Virtual field: llm_api_key maps to {provider}_api_key in overrides.
_VIRTUAL_API_KEY = "llm_api_key"  # pragma: allowlist secret


# Fields that use multi-select (checkbox) instead of single-select (radio).
def _get_multi_select_fields() -> dict[str, list[str]]:
    """Build multi-select options at runtime so custom agents are included."""
    return {"default_agents": [*AGENT_REGISTRY.keys(), "all"]}


def _get_model_choices(session_or_overrides: dict[str, str], settings: Settings) -> list[str]:
    """Return model IDs for the currently active provider from the registry."""
    provider_key = session_or_overrides.get(
        "llm_provider",
        str(getattr(settings, "llm_provider", "nvidia")),
    )
    try:
        provider_info = get_provider(provider_key)
        return [m.id for m in provider_info.models]
    except KeyError:
        return []


# Alias for local readability -- actual data lives in config_categories.py.
_CATEGORIES = CONFIG_CATEGORIES


# ---------------------------------------------------------------------------
# Field introspection helpers
# ---------------------------------------------------------------------------


def _get_field_info(key: str) -> FieldInfo | None:
    """Get the Pydantic FieldInfo for a settings key."""
    return Settings.model_fields.get(key)


def _get_field_type(key: str) -> type | None:
    """Get the resolved Python type for a settings key."""
    resolved_hints = typing.get_type_hints(Settings)
    raw = resolved_hints.get(key)
    if raw is None:
        return None
    if isinstance(raw, types.UnionType):
        non_none = [a for a in raw.__args__ if a is not type(None)]
        if non_none:
            result: type = non_none[0]
            return result
        return None
    if isinstance(raw, type):
        return raw
    return None


def _get_enum_choices(field_type: type) -> list[str] | None:
    """Return enum member values if the type is an enum, else None."""
    if isinstance(field_type, EnumType):
        return [str(member.value) for member in field_type]  # type: ignore[var-annotated]
    return None


def _is_bool_field(field_type: type | None) -> bool:
    return field_type is bool


def _is_secret_field(key: str) -> bool:
    if key == _VIRTUAL_API_KEY:
        return True
    resolved_hints = typing.get_type_hints(Settings)
    raw = resolved_hints.get(key)
    if raw is SecretStr:
        return True
    if isinstance(raw, types.UnionType):
        return any(a is SecretStr for a in raw.__args__)
    return False


def _format_display_value(key: str, value: str) -> str:
    """Format a value for display, masking secrets."""
    if _is_secret_field(key) and value and value != "None":
        return _mask_secret_str(value)
    return value


def _mask_secret_str(raw: str) -> str:
    """Mask a secret string for display (shared logic with config_cmd._mask_secret)."""
    if len(raw) <= 8:
        return "****"
    return f"{raw[:4]}****{raw[-4:]}"


def _validate_field(key: str, raw_value: str) -> tuple[bool, str]:
    """Validate a single field value. Returns (is_valid, error_message)."""
    field_type = _get_field_type(key)

    if raw_value in ("", "None"):
        info = _get_field_info(key)
        if info is not None and info.default is None:
            return True, ""
        resolved = typing.get_type_hints(Settings).get(key)
        if isinstance(resolved, types.UnionType) and type(None) in resolved.__args__:
            return True, ""

    if field_type is None:
        return True, ""

    if _is_bool_field(field_type):
        if raw_value.lower() not in ("true", "false", "1", "0", "yes", "no"):
            return False, f"Must be true/false, got: {raw_value}"
        return True, ""

    if field_type is int:
        try:
            parsed = int(raw_value)
        except ValueError:
            return False, f"Must be an integer, got: {raw_value}"
        info = _get_field_info(key)
        if info is not None and info.metadata:
            for m in info.metadata:
                if hasattr(m, "ge") and parsed < m.ge:
                    return False, f"Must be >= {m.ge}, got: {parsed}"
                if hasattr(m, "le") and parsed > m.le:
                    return False, f"Must be <= {m.le}, got: {parsed}"
        return True, ""

    if field_type is float:
        try:
            parsed_f = float(raw_value)
        except ValueError:
            return False, f"Must be a number, got: {raw_value}"
        info = _get_field_info(key)
        if info is not None and info.metadata:
            for m in info.metadata:
                if hasattr(m, "ge") and parsed_f < m.ge:
                    return False, f"Must be >= {m.ge}, got: {parsed_f}"
                if hasattr(m, "le") and parsed_f > m.le:
                    return False, f"Must be <= {m.le}, got: {parsed_f}"
        return True, ""

    choices = _get_enum_choices(field_type)
    if choices is not None and raw_value not in choices:
        return False, f"Must be one of: {', '.join(choices)}"

    return True, ""


# ---------------------------------------------------------------------------
# Edit mode enum
# ---------------------------------------------------------------------------


class _EditMode:
    """Editor mode constants."""

    NAVIGATE = "navigate"
    TEXT = "text"
    SELECT = "select"
    MULTI_SELECT = "multi_select"


# ---------------------------------------------------------------------------
# ConfigEditor
# ---------------------------------------------------------------------------


class ConfigEditor:
    """Interactive config editor with selector sub-screens."""

    def __init__(self, session: SessionState) -> None:
        self.session = session
        self.rows: list[tuple[str, str]] = []
        self.values: dict[str, str] = {}
        self.cursor: int = 0
        self.has_changes: bool = False
        self.changed_keys: set[str] = set()
        self.error_message: str = ""
        self.status_message: str = ""

        # Text edit state
        self.edit_buffer: str = ""
        self.edit_cursor_pos: int = 0

        # Selector state (single-select radio)
        self.selector_choices: list[str] = []
        self.selector_cursor: int = 0
        self.selector_key: str = ""

        # Multi-select state (checkboxes)
        self.multi_checked: list[bool] = []

        self.original_values: dict[str, str] = {}
        self.mode: str = _EditMode.NAVIGATE
        self._build_rows()
        # Snapshot original values after build so we can detect real changes
        self.original_values = dict(self.values)

    def _provider_api_key_field(self) -> str:
        """Return the actual config key for the current provider's API key."""
        provider = self.session.config_overrides.get(
            "llm_provider",
            str(getattr(self.session.settings, "llm_provider", "nvidia")),
        )
        return f"{provider}_api_key"  # pragma: allowlist secret

    def _build_rows(self) -> None:
        for cat_name, keys in _CATEGORIES:
            self.rows.append((cat_name, "header"))
            for key in keys:
                if key in _EXCLUDED_FIELDS:
                    continue
                self.rows.append((key, "field"))

                # Virtual llm_api_key: resolve from all sources
                if key == _VIRTUAL_API_KEY:
                    resolved = self.session.resolve_api_key_display()
                    self.values[key] = resolved if resolved else "None"
                    continue

                if key in self.session.config_overrides:
                    self.values[key] = self.session.config_overrides[key]
                else:
                    raw = getattr(self.session.settings, key, None)
                    if isinstance(raw, SecretStr):
                        self.values[key] = raw.get_secret_value()
                    elif raw is None:
                        self.values[key] = "None"
                    else:
                        self.values[key] = str(raw)

        for i, (_name, kind) in enumerate(self.rows):
            if kind == "field":
                self.cursor = i
                break

    def _current_key(self) -> str | None:
        if 0 <= self.cursor < len(self.rows):
            name, kind = self.rows[self.cursor]
            if kind == "field":
                return name
        return None

    def _apply_value(self, key: str, value: str) -> None:
        original = self.original_values.get(key, "")
        self.values[key] = value
        self.error_message = ""

        # Virtual llm_api_key: save directly to DB and env, not config_overrides
        if key == _VIRTUAL_API_KEY:
            if value != original and value and value != "None":
                import os

                real_key = self._provider_api_key_field()
                os.environ[real_key.upper()] = value
                try:
                    from code_review_agent.storage import ReviewStorage

                    storage = ReviewStorage(self.session.effective_settings.history_db_path)
                    storage.save_config(real_key, value)
                except Exception:  # noqa: S110
                    pass
            self.changed_keys.add(key) if value != original else self.changed_keys.discard(key)
            self.has_changes = bool(self.changed_keys)
            self.session.invalidate_settings_cache()
            return

        if value == original:
            self.session.config_overrides.pop(key, None)
            self.changed_keys.discard(key)
            self.has_changes = bool(self.changed_keys)
            return

        self.session.config_overrides[key] = value
        self.changed_keys.add(key)
        self.has_changes = True

    def _apply_provider_cascade(self, provider_value: str) -> None:
        """When provider changes, auto-update base_url, model, and api_key display."""
        try:
            provider_info = get_provider(provider_value)
        except KeyError:
            return

        # Set base_url to the provider's URL from registry
        self._apply_value("llm_base_url", provider_info.base_url)

        # Update model to the new provider's default free model
        self._apply_value("llm_model", provider_info.default_model)

        # Refresh the virtual llm_api_key to show the new provider's key
        resolved = self.session.resolve_api_key_display(provider_value)
        self.values[_VIRTUAL_API_KEY] = resolved if resolved else "None"
        # Update original so the * indicator is correct for the new provider
        self.original_values[_VIRTUAL_API_KEY] = self.values[_VIRTUAL_API_KEY]

        self.status_message = (
            f"Provider -> {provider_value}, "
            f"model -> {provider_info.default_model}, "
            f"base_url -> {provider_info.base_url}"
        )

    # --- Navigation ---

    def move_up(self) -> None:
        if self.mode == _EditMode.NAVIGATE:
            i = self.cursor - 1
            while i >= 0:
                if self.rows[i][1] == "field":
                    self.cursor = i
                    return
                i -= 1
        elif self.mode in (_EditMode.SELECT, _EditMode.MULTI_SELECT):
            self.selector_cursor = max(0, self.selector_cursor - 1)

    def move_down(self) -> None:
        if self.mode == _EditMode.NAVIGATE:
            i = self.cursor + 1
            while i < len(self.rows):
                if self.rows[i][1] == "field":
                    self.cursor = i
                    return
                i += 1
        elif self.mode in (_EditMode.SELECT, _EditMode.MULTI_SELECT):
            self.selector_cursor = min(
                len(self.selector_choices) - 1,
                self.selector_cursor + 1,
            )

    # --- Edit entry points ---

    def start_edit(self) -> None:
        key = self._current_key()
        if key is None:
            return

        field_type = _get_field_type(key)
        self.error_message = ""

        # Bool: toggle immediately
        if _is_bool_field(field_type):
            current = self.values[key].lower()
            new_val = "false" if current in ("true", "1", "yes") else "true"
            self._apply_value(key, new_val)
            return

        # Multi-select field
        if key in _get_multi_select_fields():
            self._open_multi_select(key)
            return

        # Provider field: open selector with all registered providers
        if key == "llm_provider":
            from code_review_agent.providers import PROVIDER_REGISTRY

            provider_choices = sorted(PROVIDER_REGISTRY.keys())
            self._open_selector(key, provider_choices)
            return

        # Model field: open selector with provider's models
        if key == "llm_model":
            model_choices = _get_model_choices(
                self.session.config_overrides, self.session.settings
            )
            if model_choices:
                self._open_selector(key, model_choices)
                return

        # Enum: open single-select sub-screen
        choices = _get_enum_choices(field_type) if field_type else None
        if choices:
            self._open_selector(key, choices)
            return

        # Text/number: inline edit
        self.mode = _EditMode.TEXT
        self.edit_buffer = self.values.get(key, "")
        if self.edit_buffer == "None":
            self.edit_buffer = ""
        self.edit_cursor_pos = len(self.edit_buffer)

    def _open_selector(self, key: str, choices: list[str]) -> None:
        self.mode = _EditMode.SELECT
        self.selector_key = key
        self.selector_choices = choices
        current = self.values.get(key, "")
        try:
            self.selector_cursor = choices.index(current)
        except ValueError:
            self.selector_cursor = 0

    def _open_multi_select(self, key: str) -> None:
        self.mode = _EditMode.MULTI_SELECT
        self.selector_key = key
        self.selector_choices = _get_multi_select_fields()[key]
        current_csv = self.values.get(key, "")
        selected = {s.strip() for s in current_csv.split(",") if s.strip()}

        self.multi_checked = []
        for choice in self.selector_choices:
            if choice == "all":
                # "all" is checked if all real agents are selected
                real_agents = [c for c in self.selector_choices if c != "all"]
                self.multi_checked.append(all(a in selected for a in real_agents))
            else:
                self.multi_checked.append(choice in selected)

        self.selector_cursor = 0

    # --- Selector actions ---

    def select_confirm(self) -> None:
        """Confirm selection in single-select mode."""
        if self.mode == _EditMode.SELECT:
            chosen = self.selector_choices[self.selector_cursor]
            old_value = self.values.get(self.selector_key, "")
            self._apply_value(self.selector_key, chosen)
            self.mode = _EditMode.NAVIGATE

            # Cascade: provider change -> update base_url + model
            if self.selector_key == "llm_provider" and chosen != old_value:
                self._apply_provider_cascade(chosen)

    def multi_toggle(self) -> None:
        """Toggle checkbox in multi-select mode."""
        if self.mode != _EditMode.MULTI_SELECT:
            return

        idx = self.selector_cursor
        choice = self.selector_choices[idx]

        if choice == "all":
            # Toggle all: if all checked, uncheck all; otherwise check all
            all_checked = all(
                self.multi_checked[i] for i, c in enumerate(self.selector_choices) if c != "all"
            )
            new_state = not all_checked
            for i, c in enumerate(self.selector_choices):
                if c == "all":
                    self.multi_checked[i] = new_state
                else:
                    self.multi_checked[i] = new_state
        else:
            self.multi_checked[idx] = not self.multi_checked[idx]
            # Update "all" checkbox
            all_idx = None
            for i, c in enumerate(self.selector_choices):
                if c == "all":
                    all_idx = i
                    break
            if all_idx is not None:
                real_all_checked = all(
                    self.multi_checked[i]
                    for i, c in enumerate(self.selector_choices)
                    if c != "all"
                )
                self.multi_checked[all_idx] = real_all_checked

    def multi_confirm(self) -> None:
        """Confirm multi-select and apply value.

        Validates against max_concurrent_agents when selecting agents.
        """
        if self.mode != _EditMode.MULTI_SELECT:
            return

        selected = [
            c for i, c in enumerate(self.selector_choices) if self.multi_checked[i] and c != "all"
        ]

        # Enforce max_concurrent_agents for agent selection
        if self.selector_key == "default_agents" and selected:
            max_agents = getattr(
                self.session.settings,
                "max_concurrent_agents",
                4,
            )
            # Also check session overrides
            override = self.session.config_overrides.get(
                "max_concurrent_agents",
            )
            if override is not None:
                with contextlib.suppress(ValueError):
                    max_agents = int(override)

            if len(selected) > max_agents:
                self.error_message = (
                    f"Selected {len(selected)} agents but max_concurrent_agents is {max_agents}"
                )
                return

        csv_value = ",".join(selected)
        self._apply_value(self.selector_key, csv_value)
        self.error_message = ""
        self.mode = _EditMode.NAVIGATE

    # --- Text edit actions ---

    def confirm_edit(self) -> None:
        key = self._current_key()
        if key is None:
            return
        raw = self.edit_buffer.strip()
        is_valid, err = _validate_field(key, raw)
        if not is_valid:
            self.error_message = err
            return
        self._apply_value(key, raw if raw not in ("", "None") else "None")
        self.mode = _EditMode.NAVIGATE

    def cancel_edit(self) -> None:
        self.mode = _EditMode.NAVIGATE
        self.error_message = ""

    # --- Rendering ---

    def render(self) -> FormattedText:
        if self.mode in (_EditMode.SELECT, _EditMode.MULTI_SELECT):
            return self._render_selector()
        return self._render_main()

    def _render_main(self) -> FormattedText:
        lines: list[tuple[str, str]] = []

        lines.append(("bold", " Config Editor"))
        lines.append(("", "  ("))
        lines.append(("cyan", "Up/Down"))
        lines.append(("", " navigate, "))
        lines.append(("cyan", "Enter"))
        lines.append(("", " edit, "))
        lines.append(("cyan", "Tab"))
        lines.append(("", " save, "))
        lines.append(("cyan", "Esc/q"))
        lines.append(("", " exit)\n"))

        if self.status_message:
            if self.status_message.startswith("!"):
                lines.append((theme.error, f"  {self.status_message}\n"))
            else:
                lines.append((theme.success, f"  {self.status_message}\n"))
            self.status_message = ""
        elif self.has_changes:
            lines.append((theme.warning, "  * unsaved changes (Tab to save)\n"))
        lines.append(("", "\n"))

        visible_start = max(0, self.cursor - 15)
        visible_end = min(len(self.rows), visible_start + 35)

        for i in range(visible_start, visible_end):
            name, kind = self.rows[i]
            is_selected = i == self.cursor

            if kind == "header":
                lines.append(("", "\n"))
                lines.append(("bold underline", f"  {name}\n"))
                continue

            key = name
            field_type = _get_field_type(key)

            # Prefix
            if is_selected:
                lines.append(("reverse bold", " > "))
            else:
                lines.append(("", "   "))

            # Key name
            key_style = "bold cyan" if is_selected else ""
            lines.append((key_style, f"{key:<38}"))

            # Value column
            if is_selected and self.mode == _EditMode.TEXT:
                lines.append(("", " "))
                pos = self.edit_cursor_pos
                before = self.edit_buffer[:pos]
                after = self.edit_buffer[pos:]
                lines.append(("bg:ansiwhite fg:ansiblack", before))
                # Blinking cursor character at the current position
                cursor_char = after[0] if after else " "
                lines.append(("bg:ansicyan fg:ansiblack blink", cursor_char))
                if len(after) > 1:
                    lines.append(("bg:ansiwhite fg:ansiblack", after[1:]))
            elif _is_bool_field(field_type):
                val = self.values.get(key, "false")
                is_true = val.lower() in ("true", "1", "yes")
                if is_true:
                    style = "green bold" if is_selected else "green"
                    lines.append((style, " [ON]  off "))
                else:
                    style = "bold" if is_selected else "dim"
                    lines.append((style, "  on  [OFF]"))
            else:
                # Enum, multi-select, text -- all show just the current value
                raw_val = self.values.get(key, "")
                display = _format_display_value(key, raw_val)

                # For multi-select, show as readable list
                if key in _get_multi_select_fields():
                    if raw_val and raw_val not in ("None", ""):
                        display = raw_val.replace(",", ", ")
                    else:
                        display = "tier defaults"
                elif not display or display == "None":
                    display = "not set"

                is_changed = key in self.changed_keys
                is_placeholder = display in ("not set", "tier defaults")
                if not display or is_placeholder:
                    val_style = "dim" if not is_selected else "bold dim"
                elif is_selected:
                    val_style = "bold"
                else:
                    val_style = ""
                lines.append((val_style, f" {display}"))
                if is_changed:
                    lines.append((theme.warning, " *"))

            lines.append(("", "\n"))

        # Status line
        lines.append(("", "\n"))
        if self.error_message:
            lines.append(("bg:ansired fg:ansiwhite bold", f"  Error: {self.error_message}  "))
            lines.append(("", "\n"))
        elif self.mode == _EditMode.TEXT:
            lines.append(("dim", "  Type value, Enter to confirm, Esc to cancel"))
            lines.append(("", "\n"))
        else:
            current_key = self._current_key()
            if current_key:
                info = _get_field_info(current_key)
                hints: list[str] = []
                if info and info.metadata:
                    for m in info.metadata:
                        if hasattr(m, "ge"):
                            hints.append(f"min={m.ge}")
                        if hasattr(m, "le"):
                            hints.append(f"max={m.le}")
                if info and info.default is not None:
                    hints.append(f"default={info.default}")
                if hints:
                    lines.append(("dim", f"  {', '.join(hints)}"))
                    lines.append(("", "\n"))

        return FormattedText(lines)

    def _render_selector(self) -> FormattedText:
        lines: list[tuple[str, str]] = []
        is_multi = self.mode == _EditMode.MULTI_SELECT

        # Title
        label = self.selector_key.replace("_", " ").title()
        lines.append(("bold", f" Select: {label}\n"))

        if is_multi:
            lines.append(("dim", "  Enter/Space: toggle, Tab: confirm, Esc: cancel\n"))
        else:
            lines.append(("dim", "  Up/Down to navigate, Enter to select, Esc to cancel\n"))
        lines.append(("", "\n"))

        # For model selector, show model name annotations from registry
        model_names: dict[str, str] = {}
        if self.selector_key == "llm_model":
            provider_key = self.session.config_overrides.get(
                "llm_provider",
                str(getattr(self.session.settings, "llm_provider", "nvidia")),
            )
            try:
                provider_info = get_provider(provider_key)
                for m in provider_info.models:
                    label_parts = [m.name]
                    if m.is_free:
                        label_parts.append("free")
                    label_parts.append(f"{m.context_window // 1000}k ctx")
                    model_names[m.id] = " | ".join(label_parts)
            except KeyError:
                pass

        # Load health status for "(not working)" labels
        from code_review_agent.interactive.repl import get_health_status

        health = get_health_status(self.session)
        broken_models = health.get("model", set())
        broken_providers = health.get("provider", set())

        for i, choice in enumerate(self.selector_choices):
            is_cursor = i == self.selector_cursor

            # Prefix: cursor indicator
            if is_cursor:
                lines.append(("bold cyan", " > "))
            else:
                lines.append(("", "   "))

            # Checkbox or radio
            if is_multi:
                checked = self.multi_checked[i] if i < len(self.multi_checked) else False
                if checked:
                    lines.append(("green bold", "[x] "))
                else:
                    lines.append(("", "[ ] "))
            else:
                current_val = self.values.get(self.selector_key, "")
                if choice == current_val:
                    lines.append(("green bold", "(*) "))
                else:
                    lines.append(("", "( ) "))

            # Choice label
            style = "bold" if is_cursor else ""

            # Determine if this choice is marked as broken
            is_model_broken = self.selector_key == "llm_model" and choice in broken_models
            is_prov_broken = self.selector_key == "llm_provider" and choice in broken_providers
            is_broken = is_model_broken or is_prov_broken

            # Special formatting for "all"
            if choice == "all":
                lines.append((style, "all (select/deselect all)"))
            elif choice in model_names:
                lines.append((style, f"{choice}  "))
                lines.append(("dim", f"({model_names[choice]})"))
            else:
                lines.append((style, choice))

            # Show health and current status (broken replaces current)
            if is_broken:
                lines.append(("red bold", " (not working)"))
            elif not is_multi:
                current_val = self.values.get(self.selector_key, "")
                if choice == current_val:
                    lines.append(("green", " (current)"))

            lines.append(("", "\n"))

        # Show selected count for multi-select
        if is_multi:
            checked_count = sum(
                1
                for i, c in enumerate(self.selector_choices)
                if self.multi_checked[i] and c != "all"
            )
            lines.append(("", "\n"))
            lines.append(("dim", f"  {checked_count} selected"))

            max_agents = getattr(
                self.session.settings,
                "max_concurrent_agents",
                4,
            )
            override = self.session.config_overrides.get(
                "max_concurrent_agents",
            )
            if override is not None:
                with contextlib.suppress(ValueError):
                    max_agents = int(override)
            lines.append(("dim", f" (max: {max_agents})"))
            lines.append(("", "\n"))

        # Error line
        if self.error_message:
            lines.append(("", "\n"))
            lines.append(
                (
                    "bg:ansired fg:ansiwhite bold",
                    f"  {self.error_message}  ",
                )
            )
            lines.append(("", "\n"))

        return FormattedText(lines)


# ---------------------------------------------------------------------------
# Key bindings and application
# ---------------------------------------------------------------------------


def cmd_config_edit(args: list[str], session: SessionState) -> None:
    """Launch the interactive config editor."""
    editor = ConfigEditor(session)

    kb = KeyBindings()

    @kb.add("up")
    def on_up(event: KeyPressEvent) -> None:
        if editor.mode != _EditMode.TEXT:
            editor.move_up()

    @kb.add("down")
    def on_down(event: KeyPressEvent) -> None:
        if editor.mode != _EditMode.TEXT:
            editor.move_down()

    @kb.add("enter")
    def on_enter(event: KeyPressEvent) -> None:
        if editor.mode == _EditMode.TEXT:
            editor.confirm_edit()
        elif editor.mode == _EditMode.SELECT:
            editor.select_confirm()
        elif editor.mode == _EditMode.MULTI_SELECT:
            # Enter toggles in multi-select (not confirm)
            editor.multi_toggle()
        else:
            editor.start_edit()

    @kb.add("space")
    def on_space(event: KeyPressEvent) -> None:
        if editor.mode == _EditMode.TEXT:
            editor.edit_buffer = (
                editor.edit_buffer[: editor.edit_cursor_pos]
                + " "
                + editor.edit_buffer[editor.edit_cursor_pos :]
            )
            editor.edit_cursor_pos += 1
        elif editor.mode == _EditMode.MULTI_SELECT:
            editor.multi_toggle()
        elif editor.mode == _EditMode.SELECT:
            editor.select_confirm()
        else:
            editor.start_edit()

    @kb.add("tab")
    def on_tab(event: KeyPressEvent) -> None:
        if editor.mode == _EditMode.MULTI_SELECT:
            editor.multi_confirm()
        elif editor.mode == _EditMode.NAVIGATE:
            from code_review_agent.interactive.commands.config_cmd import (
                save_config_to_db,
            )

            if session.config_overrides:
                saved = save_config_to_db(session)
                if saved:
                    editor.status_message = f"Saved {saved} setting(s) to database"
                    editor.has_changes = False
                else:
                    editor.status_message = "! Failed to save"
            else:
                editor.status_message = "No changes to save"

    @kb.add("escape")
    def on_escape(event: KeyPressEvent) -> None:
        if editor.mode in (_EditMode.TEXT, _EditMode.SELECT, _EditMode.MULTI_SELECT):
            editor.cancel_edit()
        else:
            event.app.exit()

    @kb.add("q")
    def on_quit(event: KeyPressEvent) -> None:
        if editor.mode == _EditMode.TEXT:
            editor.edit_buffer = (
                editor.edit_buffer[: editor.edit_cursor_pos]
                + "q"
                + editor.edit_buffer[editor.edit_cursor_pos :]
            )
            editor.edit_cursor_pos += 1
        elif editor.mode in (_EditMode.SELECT, _EditMode.MULTI_SELECT):
            editor.cancel_edit()
        else:
            event.app.exit()

    @kb.add("left")
    def on_left(event: KeyPressEvent) -> None:
        if editor.mode == _EditMode.TEXT:
            editor.edit_cursor_pos = max(0, editor.edit_cursor_pos - 1)

    @kb.add("right")
    def on_right(event: KeyPressEvent) -> None:
        if editor.mode == _EditMode.TEXT:
            editor.edit_cursor_pos = min(
                len(editor.edit_buffer),
                editor.edit_cursor_pos + 1,
            )

    @kb.add("backspace")
    def on_backspace(event: KeyPressEvent) -> None:
        if editor.mode == _EditMode.TEXT and editor.edit_cursor_pos > 0:
            editor.edit_buffer = (
                editor.edit_buffer[: editor.edit_cursor_pos - 1]
                + editor.edit_buffer[editor.edit_cursor_pos :]
            )
            editor.edit_cursor_pos -= 1

    @kb.add("delete")
    def on_delete(event: KeyPressEvent) -> None:
        if editor.mode == _EditMode.TEXT and editor.edit_cursor_pos < len(editor.edit_buffer):
            editor.edit_buffer = (
                editor.edit_buffer[: editor.edit_cursor_pos]
                + editor.edit_buffer[editor.edit_cursor_pos + 1 :]
            )

    @kb.add("<any>")
    def on_char(event: KeyPressEvent) -> None:
        if editor.mode != _EditMode.TEXT:
            return
        # Accept single keystrokes and multi-character paste data
        printable = "".join(c for c in event.data if c.isprintable())
        if printable:
            editor.edit_buffer = (
                editor.edit_buffer[: editor.edit_cursor_pos]
                + printable
                + editor.edit_buffer[editor.edit_cursor_pos :]
            )
            editor.edit_cursor_pos += len(printable)

    control = FormattedTextControl(editor.render)
    window = Window(content=control, wrap_lines=True)
    layout = Layout(HSplit([window]))

    app: Application[None] = Application(
        layout=layout,
        key_bindings=kb,
        full_screen=True,
        refresh_interval=0.1,
    )
    app.run()

    from rich.console import Console

    con = Console()
    if not editor.changed_keys:
        return

    session.invalidate_settings_cache()
    con.print(
        f"  [green]{len(editor.changed_keys)} setting(s) changed:[/green] "
        + ", ".join(sorted(editor.changed_keys))
    )

    # Auto-save changed keys to DB; delete reverted keys from DB
    from code_review_agent.interactive.commands.config_cmd import save_config_to_db
    from code_review_agent.storage import ReviewStorage

    saved = save_config_to_db(session)

    # Remove keys that were reverted to original from the DB
    try:
        settings = session.effective_settings
        storage = ReviewStorage(settings.history_db_path)
        for key in list(editor.original_values.keys()):
            if key not in editor.changed_keys and key not in session.config_overrides:
                storage.delete_config(key)
    except Exception:  # noqa: S110
        pass

    if saved:
        con.print(f"  [dim]{saved} setting(s) saved to database (persisted).[/dim]")

    _show_cost_warning(con, session)

    # Run connection test only if LLM-related config was changed in this session
    _LLM_KEYS = {
        "llm_provider",
        "llm_model",
        "llm_base_url",
        "nvidia_api_key",
        "openrouter_api_key",
    }
    llm_changed = editor.changed_keys & _LLM_KEYS
    if llm_changed and session.effective_settings.test_connection_on_start:
        from code_review_agent.interactive.repl import run_connection_test

        # Pass original values so we can revert on failure
        prev = {
            k: editor.original_values.get(k, "") for k in _LLM_KEYS if k in editor.original_values
        }
        run_connection_test(session, previous_llm_config=prev)
    elif not llm_changed:
        con.print("  [dim]No LLM changes, skipping connection test.[/dim]")


def _show_cost_warning(con: object, session: SessionState) -> None:
    """Show cost impact warning if cost-increasing overrides are active."""
    multiplier, reasons = session.estimate_cost_multiplier()
    if multiplier <= 1.0:
        return

    from rich.console import Console

    if not isinstance(con, Console):
        return

    con.print()
    con.print(f"  [{theme.warning}]! Cost impact: ~{multiplier:.1f}x per review[/{theme.warning}]")
    for reason in reasons:
        con.print(f"    [dim]{reason}[/dim]")
