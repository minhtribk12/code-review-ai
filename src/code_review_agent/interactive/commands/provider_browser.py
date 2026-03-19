"""Full-screen provider/model browser with expand, delete, edit, and add."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout

import code_review_agent.providers as _providers_mod
from code_review_agent.providers import get_provider, reload_registry
from code_review_agent.theme import theme

if TYPE_CHECKING:
    from prompt_toolkit.key_binding import KeyPressEvent

    from code_review_agent.interactive.session import SessionState

_Lines = list[tuple[str, str]]


class _Mode(StrEnum):
    NAVIGATE = "navigate"
    CONFIRM_DELETE = "confirm_delete"
    FIELD_SELECT = "field_select"
    EDIT_FIELD = "edit_field"
    ADD_INPUT = "add_input"


class _RowKind(StrEnum):
    PROVIDER = "provider"
    MODEL = "model"


class _Row:
    """A row in the browser representing either a provider or a model."""

    __slots__ = ("is_custom", "kind", "model_id", "provider_key")

    def __init__(
        self,
        kind: _RowKind,
        provider_key: str,
        model_id: str = "",
        *,
        is_custom: bool = False,
    ) -> None:
        self.kind = kind
        self.provider_key = provider_key
        self.model_id = model_id
        self.is_custom = is_custom


# Steps for multi-field add wizards
_ADD_PROVIDER_STEPS = ["name", "base_url", "api_key", "rate_limit_rpm"]
_ADD_MODEL_STEPS = ["model_name", "label", "is_free", "context_window"]


class ProviderBrowser:
    """State for the full-screen provider/model browser."""

    def __init__(self, session: SessionState) -> None:
        self.session = session
        self.cursor: int = 0
        self.expanded: set[str] = set()
        self.rows: list[_Row] = []
        self.mode: _Mode = _Mode.NAVIGATE
        self.status_message: str = ""
        self.confirm_target: _Row | None = None

        # Edit state
        self.edit_field_name: str = ""
        self.edit_buffer: str = ""
        self.edit_cursor_pos: int = 0

        # Field selector state
        self.field_choices: list[tuple[str, str]] = []  # (field_key, current_value)
        self.field_cursor: int = 0

        # Add wizard state
        self.add_kind: str = ""  # "provider" or "model"
        self.add_steps: list[str] = []
        self.add_step_index: int = 0
        self.add_data: dict[str, str] = {}
        self.add_target_provider: str = ""  # for adding models
        self.add_error: str = ""

        self._rebuild_rows()

    # -- Helpers ---------------------------------------------------------------

    def _user_providers(self) -> set[str]:
        from code_review_agent.interactive.commands.provider_cmd import (
            _load_user_registry,
        )

        return set(_load_user_registry().keys())

    def _rebuild_rows(self) -> None:
        self.rows.clear()
        user_keys = self._user_providers()
        for key in sorted(_providers_mod.PROVIDER_REGISTRY.keys()):
            is_custom = key in user_keys
            self.rows.append(_Row(_RowKind.PROVIDER, key, is_custom=is_custom))
            if key in self.expanded:
                info = get_provider(key)
                for m in info.models:
                    self.rows.append(_Row(_RowKind.MODEL, key, m.id, is_custom=is_custom))
        if self.cursor >= len(self.rows):
            self.cursor = max(0, len(self.rows) - 1)

    # -- Navigation ------------------------------------------------------------

    def move_up(self) -> None:
        self.cursor = max(0, self.cursor - 1)

    def move_down(self) -> None:
        self.cursor = min(len(self.rows) - 1, self.cursor + 1)

    def toggle_expand(self) -> None:
        if not self.rows:
            return
        row = self.rows[self.cursor]
        if row.kind == _RowKind.PROVIDER:
            if row.provider_key in self.expanded:
                self.expanded.discard(row.provider_key)
            else:
                self.expanded.add(row.provider_key)
            self._rebuild_rows()

    # -- Delete ----------------------------------------------------------------

    def request_delete(self) -> None:
        if not self.rows:
            return
        row = self.rows[self.cursor]
        if not row.is_custom:
            self.status_message = "Cannot delete built-in entries"
            return
        self.confirm_target = row
        self.mode = _Mode.CONFIRM_DELETE

    def confirm_delete(self) -> None:
        target = self.confirm_target
        if target is None:
            self.mode = _Mode.NAVIGATE
            return

        from code_review_agent.interactive.commands.provider_cmd import (
            _load_user_registry,
            _save_user_registry,
        )

        user_providers = _load_user_registry()

        if target.kind == _RowKind.PROVIDER:
            if target.provider_key in user_providers:
                del user_providers[target.provider_key]
                _save_user_registry(user_providers)
                reload_registry()
                self.expanded.discard(target.provider_key)
                self.status_message = f"Deleted provider '{target.provider_key}'"
        elif target.kind == _RowKind.MODEL:
            prov = user_providers.get(target.provider_key)
            if isinstance(prov, dict) and "models" in prov:
                prov["models"] = [m for m in prov["models"] if m.get("id") != target.model_id]
                _save_user_registry(user_providers)
                reload_registry()
                self.status_message = f"Deleted model '{target.model_id}'"

        self.confirm_target = None
        self.mode = _Mode.NAVIGATE
        self._rebuild_rows()

    # -- Edit ------------------------------------------------------------------

    def start_edit(self) -> None:
        """Open field selector for the current row."""
        if not self.rows:
            return
        row = self.rows[self.cursor]
        info = get_provider(row.provider_key)

        if row.kind == _RowKind.PROVIDER:
            self.field_choices = [
                ("base_url", info.base_url),
                ("default_model", info.default_model),
                ("rate_limit_rpm", str(info.rate_limit_rpm)),
            ]
        elif row.kind == _RowKind.MODEL:
            for m in info.models:
                if m.id == row.model_id:
                    self.field_choices = [
                        ("name", m.name),
                        ("is_free", str(m.is_free).lower()),
                        ("context_window", str(m.context_window)),
                    ]
                    break
            else:
                return

        self.field_cursor = 0
        self.mode = _Mode.FIELD_SELECT

    def select_field(self) -> None:
        """Confirm field selection and open text editor."""
        if not self.field_choices:
            return
        field_key, current_value = self.field_choices[self.field_cursor]
        self.edit_field_name = field_key
        self.edit_buffer = current_value
        self.edit_cursor_pos = len(self.edit_buffer)
        self.mode = _Mode.EDIT_FIELD

    def _ensure_user_override(self, provider_key: str) -> dict[str, Any]:
        """Ensure a provider exists in the user registry as an override.

        If the provider is built-in and not yet in the user registry,
        creates a minimal override entry so edits can be saved.
        """
        from code_review_agent.interactive.commands.provider_cmd import (
            _load_user_registry,
        )

        user_providers = _load_user_registry()
        if provider_key not in user_providers:
            # Create override entry for built-in provider
            info = get_provider(provider_key)
            user_providers[provider_key] = {
                "base_url": info.base_url,
                "rate_limit_rpm": info.rate_limit_rpm,
            }
        return user_providers

    def _coerce_field_value(self, field: str, raw: str) -> tuple[object, str]:
        """Coerce a raw string to the correct type. Returns (value, error)."""
        if field in ("rate_limit_rpm", "context_window"):
            try:
                return int(raw), ""
            except ValueError:
                return None, "Must be a number"
        if field == "is_free":
            if raw.lower() in ("true", "yes", "y", "1"):
                return True, ""
            if raw.lower() in ("false", "no", "n", "0"):
                return False, ""
            return None, "Must be true/false or yes/no"
        if field == "base_url" and not raw.startswith(("http://", "https://")):
            return None, "Must start with http:// or https://"
        return raw, ""

    def confirm_edit(self) -> None:
        """Save the inline edit to user registry."""
        if not self.rows:
            self.mode = _Mode.NAVIGATE
            return
        row = self.rows[self.cursor]
        raw_value = self.edit_buffer.strip()
        if not raw_value:
            self.status_message = "Value cannot be empty"
            return

        coerced, err = self._coerce_field_value(self.edit_field_name, raw_value)
        if err:
            self.status_message = err
            return

        from code_review_agent.interactive.commands.provider_cmd import (
            _save_user_registry,
        )

        user_providers = self._ensure_user_override(row.provider_key)
        prov = user_providers[row.provider_key]

        if row.kind == _RowKind.PROVIDER:
            prov[self.edit_field_name] = coerced
        elif row.kind == _RowKind.MODEL:
            # Ensure models list exists in user override
            if "models" not in prov:
                prov["models"] = []
            # Find and update, or add override entry
            found = False
            for m in prov["models"]:
                if m.get("id") == row.model_id:
                    m[self.edit_field_name] = coerced
                    found = True
                    break
            if not found:
                # Model only exists in bundled registry — add override
                info = get_provider(row.provider_key)
                for m in info.models:
                    if m.id == row.model_id:
                        override = {
                            "id": m.id,
                            "name": m.name,
                            "is_free": m.is_free,
                            "context_window": m.context_window,
                        }
                        override[self.edit_field_name] = coerced
                        prov["models"].append(override)
                        break

        _save_user_registry(user_providers)
        reload_registry()
        self.status_message = f"Updated {self.edit_field_name} -> {raw_value}"

        self.mode = _Mode.NAVIGATE
        self.edit_field_name = ""
        self._rebuild_rows()

        self.mode = _Mode.NAVIGATE
        self.edit_field_name = ""
        self._rebuild_rows()

    # -- Add -------------------------------------------------------------------

    def start_add_provider(self) -> None:
        self.add_kind = "provider"
        self.add_steps = list(_ADD_PROVIDER_STEPS)
        self.add_step_index = 0
        self.add_data = {}
        self.add_error = ""
        self.edit_buffer = ""
        self.edit_cursor_pos = 0
        self.mode = _Mode.ADD_INPUT

    def start_add_model(self) -> None:
        if not self.rows:
            return
        row = self.rows[self.cursor]
        provider_key = row.provider_key
        if provider_key not in self._user_providers():
            self.status_message = "Cannot add models to built-in providers"
            return
        self.add_kind = "model"
        self.add_target_provider = provider_key
        self.add_steps = list(_ADD_MODEL_STEPS)
        self.add_step_index = 0
        self.add_data = {}
        self.add_error = ""
        self.edit_buffer = ""
        self.edit_cursor_pos = 0
        self.mode = _Mode.ADD_INPUT

    def add_next_step(self) -> None:
        """Validate current input and advance to next step or save."""
        step = self.add_steps[self.add_step_index]
        value = self.edit_buffer.strip()
        self.add_error = ""

        # Validate current step
        err = self._validate_add_step(step, value)
        if err:
            self.add_error = err
            return

        # Use default if empty
        if not value:
            value = self._add_step_default(step)

        self.add_data[step] = value
        self.add_step_index += 1

        if self.add_step_index >= len(self.add_steps):
            self._save_add()
            return

        # Prepare next step
        self.edit_buffer = self._add_step_default(self.add_steps[self.add_step_index])
        self.edit_cursor_pos = len(self.edit_buffer)

    def _validate_add_step(self, step: str, value: str) -> str:
        """Return error message or empty string if valid."""
        if step == "name":
            if not value:
                return "Provider name is required"
            if value in _providers_mod.PROVIDER_REGISTRY:
                return f"Provider '{value}' already exists"
        elif step == "base_url":
            if not value:
                return "Base URL is required"
            if not value.startswith(("http://", "https://")):
                return "Must start with http:// or https://"
        elif step == "model_name":
            if not value:
                return "Model name is required"
            # Check uniqueness within provider
            if self.add_kind == "model":
                try:
                    info = get_provider(self.add_target_provider)
                    if any(m.id == value for m in info.models):
                        return f"Model '{value}' already exists"
                except KeyError:
                    pass
        elif step == "rate_limit_rpm" or step == "context_window":
            if value:
                try:
                    int(value)
                except ValueError:
                    return "Must be a number"
        elif step == "is_free":
            if value and value.lower() not in ("yes", "no", "y", "n", "true", "false"):
                return "Must be yes or no"
        return ""

    def _add_step_default(self, step: str) -> str:
        if step == "rate_limit_rpm":
            return "10"
        if step == "context_window":
            return "128000"
        if step == "is_free":
            return "yes"
        if step == "label":
            model_name = self.add_data.get("model_name", "")
            return model_name.split("/")[-1] if model_name else ""
        return ""

    def _add_step_label(self, step: str) -> str:
        labels = {
            "name": "Provider name",
            "base_url": "Base URL",
            "api_key": "API key (Enter to skip)",  # pragma: allowlist secret
            "rate_limit_rpm": "Rate limit (rpm)",
            "model_name": "Model name (API identifier)",
            "label": "Display label",
            "is_free": "Is free? (yes/no)",
            "context_window": "Context window (tokens)",
        }
        return labels.get(step, step)

    def _save_add(self) -> None:
        """Save the completed add wizard data."""

        if self.add_kind == "provider":
            self._save_add_provider()
        elif self.add_kind == "model":
            self._save_add_model()

        self.mode = _Mode.NAVIGATE
        self.add_kind = ""
        self._rebuild_rows()

    def _save_add_provider(self) -> None:
        from code_review_agent.interactive.commands.provider_cmd import (
            _load_user_registry,
            _save_user_registry,
        )

        d = self.add_data
        name = d["name"]
        rpm_str = d.get("rate_limit_rpm", "10")
        rpm = int(rpm_str) if rpm_str else 10

        user_providers = _load_user_registry()
        user_providers[name] = {
            "base_url": d["base_url"],
            "default_model": "",
            "rate_limit_rpm": rpm,
            "models": [],
        }
        _save_user_registry(user_providers)

        # Save API key if provided
        api_key = d.get("api_key", "")  # pragma: allowlist secret
        if api_key:
            import os

            from code_review_agent.storage import ReviewStorage

            storage = ReviewStorage(self.session.effective_settings.history_db_path)
            storage.save_config(f"{name}_api_key", api_key)  # pragma: allowlist secret
            os.environ[f"{name.upper()}_API_KEY"] = api_key

        reload_registry()
        self.expanded.add(name)
        self.status_message = f"Provider '{name}' added"

    def _save_add_model(self) -> None:
        from code_review_agent.interactive.commands.provider_cmd import (
            _load_user_registry,
            _save_user_registry,
        )

        d = self.add_data
        provider = self.add_target_provider
        is_free_str = d.get("is_free", "yes")
        is_free = is_free_str.lower() in ("yes", "y", "true")
        ctx_str = d.get("context_window", "128000")
        ctx = int(ctx_str) if ctx_str else 128_000

        user_providers = _load_user_registry()
        prov = user_providers.get(provider)
        if not isinstance(prov, dict):
            self.status_message = "Provider not found in user registry"
            return

        models = prov.get("models", [])
        models.append(
            {
                "id": d["model_name"],
                "name": d.get("label", d["model_name"]),
                "is_free": is_free,
                "context_window": ctx,
            }
        )
        prov["models"] = models

        # Set as default if first model
        if not prov.get("default_model"):
            prov["default_model"] = d["model_name"]

        _save_user_registry(user_providers)
        reload_registry()
        self.status_message = f"Model '{d['model_name']}' added to '{provider}'"

    # -- Cancel ----------------------------------------------------------------

    def cancel_action(self) -> None:
        self.mode = _Mode.NAVIGATE
        self.confirm_target = None
        self.edit_field_name = ""
        self.add_kind = ""

    # -- Render ----------------------------------------------------------------

    def render(self) -> FormattedText:
        lines: _Lines = []

        lines.append(("bold", " Provider Browser"))
        lines.append(("", "  ("))
        lines.append(("cyan", "Up/Down"))
        lines.append(("", " navigate, "))
        lines.append(("cyan", "Enter"))
        lines.append(("", " expand, "))
        lines.append(("cyan", "a"))
        lines.append(("", " add provider, "))
        lines.append(("cyan", "m"))
        lines.append(("", " add model, "))
        lines.append(("cyan", "d"))
        lines.append(("", " delete, "))
        lines.append(("cyan", "i"))
        lines.append(("", " edit, "))
        lines.append(("cyan", "q"))
        lines.append(("", " quit)\n"))

        if self.status_message:
            style = theme.error if "Cannot" in self.status_message else theme.success
            lines.append((style, f"  {self.status_message}\n"))
            self.status_message = ""
        lines.append(("", "\n"))

        if self.mode == _Mode.CONFIRM_DELETE:
            return self._render_confirm(lines)
        if self.mode == _Mode.FIELD_SELECT:
            return self._render_field_select(lines)
        if self.mode == _Mode.EDIT_FIELD:
            return self._render_edit(lines)
        if self.mode == _Mode.ADD_INPUT:
            return self._render_add(lines)

        health = self._load_health()
        user_keys = self._user_providers()

        visible_start = max(0, self.cursor - 15)
        visible_end = min(len(self.rows), visible_start + 30)

        for i in range(visible_start, visible_end):
            row = self.rows[i]
            is_sel = i == self.cursor

            prefix = " > " if is_sel else "   "
            lines.append(("bold cyan" if is_sel else "", prefix))

            if row.kind == _RowKind.PROVIDER:
                self._render_provider_row(lines, row, is_sel, health, user_keys)
            else:
                self._render_model_row(lines, row, is_sel, health)

            lines.append(("", "\n"))

        lines.append(("", "\n"))
        lines.append(("dim", f"  {len(self.rows)} items | {len(self.expanded)} expanded"))
        lines.append(("", "\n"))

        return FormattedText(lines)

    def _render_provider_row(
        self,
        lines: _Lines,
        row: _Row,
        is_sel: bool,
        health: dict[str, set[str]],
        user_keys: set[str],
    ) -> None:
        arrow = "v " if row.provider_key in self.expanded else "> "
        style = "bold" if is_sel else ""
        lines.append((style, arrow + row.provider_key))

        if row.provider_key in health.get("provider", set()):
            lines.append(("red bold", " (not working)"))

        info = get_provider(row.provider_key)
        label = "custom" if row.provider_key in user_keys else "built-in"
        lines.append(("dim", f"  [{label}]"))
        lines.append(("dim", f"  {info.base_url}"))
        lines.append(("dim", f"  ({len(info.models)} models)"))

    def _render_model_row(
        self,
        lines: _Lines,
        row: _Row,
        is_sel: bool,
        health: dict[str, set[str]],
    ) -> None:
        style = "bold" if is_sel else ""
        lines.append(("", "    "))
        lines.append((style, row.model_id))

        if row.model_id in health.get("model", set()):
            lines.append(("red bold", " (not working)"))

        info = get_provider(row.provider_key)
        for m in info.models:
            if m.id == row.model_id:
                free_tag = " free" if m.is_free else ""
                lines.append(("dim", f"  ({m.name}{free_tag}, {m.context_window:,} ctx)"))
                break

    def _render_field_select(self, lines: _Lines) -> FormattedText:
        """Render the field selector for editing."""
        row = self.rows[self.cursor] if self.rows else None
        if row is None:
            return FormattedText(lines)

        label = row.provider_key if row.kind == _RowKind.PROVIDER else row.model_id
        lines.append(("bold", f"\n  Edit: {label}\n"))
        lines.append(("dim", "  Select a field to edit:\n\n"))

        for i, (field_key, current_val) in enumerate(self.field_choices):
            is_sel = i == self.field_cursor
            prefix = " > " if is_sel else "   "
            style = "bold cyan" if is_sel else ""
            lines.append((style, prefix))
            lines.append((style, f"{field_key:<20}"))
            lines.append(("dim", f" {current_val}"))
            lines.append(("", "\n"))

        lines.append(("", "\n"))
        lines.append(("", "  "))
        lines.append(("cyan", "Up/Down"))
        lines.append(("", " navigate, "))
        lines.append(("cyan", "Enter"))
        lines.append(("", " edit, "))
        lines.append(("cyan", "Esc"))
        lines.append(("", " cancel\n"))

        return FormattedText(lines)

    def _render_confirm(self, lines: _Lines) -> FormattedText:
        target = self.confirm_target
        if target is None:
            return FormattedText(lines)

        if target.kind == _RowKind.PROVIDER:
            label = f"provider '{target.provider_key}'"
        else:
            label = f"model '{target.model_id}' from '{target.provider_key}'"

        lines.append(("bold", f"\n  Delete {label}?\n\n"))
        lines.append(("", "  Press "))
        lines.append(("bold cyan", "y"))
        lines.append(("", " to confirm, "))
        lines.append(("bold cyan", "n/Esc"))
        lines.append(("", " to cancel\n"))

        return FormattedText(lines)

    def _render_edit(self, lines: _Lines) -> FormattedText:
        row = self.rows[self.cursor] if self.rows else None
        if row is None:
            return FormattedText(lines)

        if row.kind == _RowKind.PROVIDER:
            label = f"{row.provider_key} -> {self.edit_field_name}"
        else:
            label = f"{row.model_id} -> {self.edit_field_name}"

        lines.append(("bold", f"\n  Edit: {label}\n\n"))
        self._render_text_input(lines)
        lines.append(("", "\n"))
        lines.append(("dim", "  Enter to save, Esc to cancel\n"))

        return FormattedText(lines)

    def _render_add(self, lines: _Lines) -> FormattedText:
        if self.add_kind == "provider":
            title = "Add Provider"
        else:
            title = f"Add Model to '{self.add_target_provider}'"

        lines.append(("bold", f"\n  {title}\n"))

        # Show completed steps
        for idx in range(self.add_step_index):
            step = self.add_steps[idx]
            val = self.add_data.get(step, "")
            display = "****" if step == "api_key" and val else val  # pragma: allowlist secret
            if not display:
                display = "(skipped)"
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

            lines.append(("", "  "))
            self._render_text_input(lines)
            lines.append(("", "\n\n"))
            lines.append(("dim", "  Enter to continue, Esc to cancel\n"))

        return FormattedText(lines)

    def _render_text_input(self, lines: _Lines) -> None:
        pos = self.edit_cursor_pos
        before = self.edit_buffer[:pos]
        after = self.edit_buffer[pos:]
        lines.append(("bg:ansiwhite fg:ansiblack", before))
        cursor_char = after[0] if after else " "
        lines.append(("bg:ansicyan fg:ansiblack blink", cursor_char))
        if len(after) > 1:
            lines.append(("bg:ansiwhite fg:ansiblack", after[1:]))

    def _load_health(self) -> dict[str, set[str]]:
        from code_review_agent.interactive.repl import get_health_status

        return get_health_status(self.session)


# ---------------------------------------------------------------------------
# Key bindings and application
# ---------------------------------------------------------------------------


def run_provider_browser(session: SessionState) -> None:
    """Launch the full-screen provider browser."""
    browser = ProviderBrowser(session)
    kb = KeyBindings()

    @kb.add("up")
    @kb.add("k")
    def on_up(_event: KeyPressEvent) -> None:
        if browser.mode == _Mode.NAVIGATE:
            browser.move_up()
        elif browser.mode == _Mode.FIELD_SELECT:
            browser.field_cursor = max(0, browser.field_cursor - 1)

    @kb.add("down")
    @kb.add("j")
    def on_down(_event: KeyPressEvent) -> None:
        if browser.mode == _Mode.NAVIGATE:
            browser.move_down()
        elif browser.mode == _Mode.FIELD_SELECT:
            browser.field_cursor = min(len(browser.field_choices) - 1, browser.field_cursor + 1)

    @kb.add("enter")
    def on_enter(_event: KeyPressEvent) -> None:
        if browser.mode == _Mode.NAVIGATE:
            browser.toggle_expand()
        elif browser.mode == _Mode.FIELD_SELECT:
            browser.select_field()
        elif browser.mode == _Mode.EDIT_FIELD:
            browser.confirm_edit()
        elif browser.mode == _Mode.ADD_INPUT:
            browser.add_next_step()

    @kb.add("space")
    def on_space(_event: KeyPressEvent) -> None:
        if browser.mode == _Mode.NAVIGATE:
            browser.toggle_expand()
        elif browser.mode in (_Mode.EDIT_FIELD, _Mode.ADD_INPUT):
            _insert_char(browser, " ")

    @kb.add("d")
    def on_delete(_event: KeyPressEvent) -> None:
        if browser.mode == _Mode.NAVIGATE:
            browser.request_delete()
        elif browser.mode in (_Mode.EDIT_FIELD, _Mode.ADD_INPUT):
            _insert_char(browser, "d")

    @kb.add("i")
    def on_edit(_event: KeyPressEvent) -> None:
        if browser.mode == _Mode.NAVIGATE:
            browser.start_edit()
        elif browser.mode in (_Mode.EDIT_FIELD, _Mode.ADD_INPUT):
            _insert_char(browser, "i")

    @kb.add("a")
    def on_add_provider(_event: KeyPressEvent) -> None:
        if browser.mode == _Mode.NAVIGATE:
            browser.start_add_provider()
        elif browser.mode in (_Mode.EDIT_FIELD, _Mode.ADD_INPUT):
            _insert_char(browser, "a")

    @kb.add("m")
    def on_add_model(_event: KeyPressEvent) -> None:
        if browser.mode == _Mode.NAVIGATE:
            browser.start_add_model()
        elif browser.mode in (_Mode.EDIT_FIELD, _Mode.ADD_INPUT):
            _insert_char(browser, "m")

    @kb.add("y")
    def on_yes(_event: KeyPressEvent) -> None:
        if browser.mode == _Mode.CONFIRM_DELETE:
            browser.confirm_delete()
        elif browser.mode in (_Mode.EDIT_FIELD, _Mode.ADD_INPUT):
            _insert_char(browser, "y")

    @kb.add("n")
    def on_no(_event: KeyPressEvent) -> None:
        if browser.mode == _Mode.CONFIRM_DELETE:
            browser.cancel_action()
        elif browser.mode in (_Mode.EDIT_FIELD, _Mode.ADD_INPUT):
            _insert_char(browser, "n")

    @kb.add("escape")
    def on_escape(event: KeyPressEvent) -> None:
        if browser.mode in (
            _Mode.CONFIRM_DELETE,
            _Mode.FIELD_SELECT,
            _Mode.EDIT_FIELD,
            _Mode.ADD_INPUT,
        ):
            browser.cancel_action()
        else:
            event.app.exit()

    @kb.add("q")
    def on_quit(event: KeyPressEvent) -> None:
        if browser.mode in (_Mode.EDIT_FIELD, _Mode.ADD_INPUT):
            _insert_char(browser, "q")
        elif browser.mode in (_Mode.CONFIRM_DELETE, _Mode.FIELD_SELECT):
            browser.cancel_action()
        else:
            event.app.exit()

    @kb.add("backspace")
    def on_backspace(_event: KeyPressEvent) -> None:
        is_editing = browser.mode in (_Mode.EDIT_FIELD, _Mode.ADD_INPUT)
        if is_editing and browser.edit_cursor_pos > 0:
            browser.edit_buffer = (
                browser.edit_buffer[: browser.edit_cursor_pos - 1]
                + browser.edit_buffer[browser.edit_cursor_pos :]
            )
            browser.edit_cursor_pos -= 1

    @kb.add("left")
    def on_left(_event: KeyPressEvent) -> None:
        if browser.mode in (_Mode.EDIT_FIELD, _Mode.ADD_INPUT):
            browser.edit_cursor_pos = max(0, browser.edit_cursor_pos - 1)

    @kb.add("right")
    def on_right(_event: KeyPressEvent) -> None:
        if browser.mode in (_Mode.EDIT_FIELD, _Mode.ADD_INPUT):
            browser.edit_cursor_pos = min(len(browser.edit_buffer), browser.edit_cursor_pos + 1)

    @kb.add("<any>")
    def on_char(event: KeyPressEvent) -> None:
        if browser.mode in (_Mode.EDIT_FIELD, _Mode.ADD_INPUT):
            printable = "".join(c for c in event.data if c.isprintable())
            if printable:
                _insert_char(browser, printable)

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


def _insert_char(browser: ProviderBrowser, text: str) -> None:
    """Insert text at cursor position in edit buffer."""
    browser.edit_buffer = (
        browser.edit_buffer[: browser.edit_cursor_pos]
        + text
        + browser.edit_buffer[browser.edit_cursor_pos :]
    )
    browser.edit_cursor_pos += len(text)
