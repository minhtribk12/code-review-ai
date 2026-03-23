"""Config commands: show, get, set, save, reset, validate."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from pydantic import SecretStr
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from code_review_agent.error_guidance import classify_exception
from code_review_agent.errors import UserError, print_error
from code_review_agent.interactive.config_categories import CONFIG_CATEGORIES_DICT
from code_review_agent.theme import theme

logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from code_review_agent.interactive.session import SessionState

console = Console()

# Alias for backwards compatibility and local readability.
_CONFIG_CATEGORIES = CONFIG_CATEGORIES_DICT


def _mask_secret(value: object) -> str:
    """Mask secret values for display."""
    if isinstance(value, SecretStr):
        from code_review_agent.interactive.commands.config_edit import _mask_secret_str

        return _mask_secret_str(value.get_secret_value())
    return str(value) if value is not None else "[dim]not set[/dim]"


def _get_config_value(session: SessionState, key: str) -> object:
    """Get a config value, checking session overrides first.

    For API key fields, uses ``session.resolve_api_key_display()`` which
    checks secrets.env, .env, and environment variables.
    """
    # Virtual llm_api_key: map to {provider}_api_key
    if key == "llm_api_key":
        val = session.resolve_api_key_display()
        if val:
            return SecretStr(val)
        return None

    if key in session.config_overrides:
        return session.config_overrides[key]

    raw = getattr(session.settings, key, None)
    # For llm_base_url, resolve from provider registry so users see the
    # actual URL that will be used instead of "not set".
    if raw is None and key == "llm_base_url":
        return session.settings.resolved_llm_base_url
    return raw


def cmd_config(args: list[str], session: SessionState) -> None:
    """Show all config or a specific category."""
    if args and args[0] == "show":
        args = args[1:]
    if args and args[0] == "edit":
        from code_review_agent.interactive.commands.config_edit import cmd_config_edit

        return cmd_config_edit(args[1:], session)
    if args and args[0] == "keys":
        from code_review_agent.interactive.commands.keys_panel import run_keys_panel

        return run_keys_panel(session)
    if args and args[0] == "get":
        return cmd_config_get(args[1:], session)
    if args and args[0] == "set":
        return cmd_config_set(args[1:], session)
    if args and args[0] == "save":
        return cmd_config_save(args[1:], session)
    if args and args[0] == "reset":
        return cmd_config_reset(args[1:], session)
    if args and args[0] == "factory-reset":
        return _cmd_factory_reset(session)
    if args and args[0] == "validate":
        return cmd_config_validate(args[1:], session)
    if args and args[0] == "diff":
        return cmd_config_diff(args[1:], session)
    if args and args[0] == "clean":
        from code_review_agent.interactive.commands.clean_cmd import cmd_config_clean

        return cmd_config_clean()

    # Show specific category
    if args:
        category = args[0].upper()
        for cat_name, keys in _CONFIG_CATEGORIES.items():
            if cat_name.upper().startswith(category):
                _print_category(cat_name, keys, session)
                return
        print_error(
            UserError(
                detail=f"Unknown category: {args[0]}",
                solution=f"Available categories: {', '.join(_CONFIG_CATEGORIES)}",
            ),
            console=console,
        )
        return

    # Show all categories
    for cat_name, keys in _CONFIG_CATEGORIES.items():
        _print_category(cat_name, keys, session)


def _print_category(name: str, keys: list[str], session: SessionState) -> None:
    """Print a config category as a Rich table."""
    table = Table(show_header=False, show_edge=False, box=None, padding=(0, 2))
    table.add_column("Key", style="bold", width=35)
    table.add_column("Value")

    for key in keys:
        value = _get_config_value(session, key)
        display = _mask_secret(value)
        is_overridden = key in session.config_overrides
        if is_overridden:
            display = f"{display} [{theme.warning}](session override)[/{theme.warning}]"
        table.add_row(key, display)

    console.print(Panel(table, title=name, border_style="blue"))


def cmd_config_get(args: list[str], session: SessionState) -> None:
    """Show a single config value."""
    if not args:
        print_error(
            UserError(
                detail="Missing argument",
                solution="Usage: config get <key>. Use 'config' to see all keys.",
            ),
            console=console,
        )
        return
    key = args[0]
    value = _get_config_value(session, key)
    if value is None and not hasattr(session.settings, key):
        print_error(
            UserError(
                detail=f"Unknown config key: {key}",
                solution="Use 'config' to see all available settings.",
            ),
            console=console,
        )
        return
    console.print(f"  {key} = {_mask_secret(value)}")


def cmd_config_set(args: list[str], session: SessionState) -> None:
    """Set a config value and persist to config.yaml immediately."""
    if len(args) < 2:
        print_error(
            UserError(
                detail="Missing argument",
                solution="Usage: config set <key> <value>. Use 'config' to see all keys.",
            ),
            console=console,
        )
        return
    key = args[0]
    value = " ".join(args[1:])

    if not hasattr(session.settings, key):
        print_error(
            UserError(
                detail=f"Unknown config key: {key}",
                solution="Use 'config' to see all available settings.",
            ),
            console=console,
        )
        return

    # API keys go directly to secrets.env (not config_overrides)
    if key.endswith("_api_key") or key == "llm_api_key":
        real_key = key
        if key == "llm_api_key":
            provider = session.effective_settings.llm_provider
            real_key = f"{provider}_api_key"  # pragma: allowlist secret
        try:
            session.save_api_key(
                real_key.removesuffix("_api_key"),
                value,
            )
            console.print(f"  [green]{key}[/green] = **** [dim](saved to secrets.env)[/dim]")
        except Exception as exc:
            print_error(
                classify_exception(exc, context="Saving API key"),
                console=console,
            )
        return

    session.config_overrides[key] = value
    session.invalidate_settings_cache()

    # Auto-save to config.yaml immediately
    saved = save_config_to_yaml(session)
    persist_label = "(saved)" if saved else "(session only)"
    console.print(f"  [green]{key}[/green] = {value} [dim]{persist_label}[/dim]")

    # Show cost warning for cost-related keys
    _COST_KEYS = {"max_deepening_rounds", "is_validation_enabled", "max_validation_rounds"}
    if key in _COST_KEYS:
        multiplier, reasons = session.estimate_cost_multiplier()
        if multiplier > 1.0:
            console.print(
                f"\n  [{theme.warning}]! Cost impact:"
                f" ~{multiplier:.1f}x per review[/{theme.warning}]"
            )
            for reason in reasons:
                console.print(f"    [dim]{reason}[/dim]")

    # Run connection test for LLM-related config changes
    _LLM_SET = {
        "llm_provider",
        "llm_model",
        "llm_base_url",
        "nvidia_api_key",
        "openrouter_api_key",
    }
    if key in _LLM_SET and session.effective_settings.test_connection_on_start:
        from code_review_agent.interactive.repl import run_connection_test

        # Build previous config snapshot for revert
        prev: dict[str, str] = {}
        for k in ("llm_provider", "llm_model", "llm_base_url"):
            old_val = getattr(session.settings, k, None)
            if old_val is not None:
                prev[k] = str(old_val)
        run_connection_test(session, previous_llm_config=prev)


def cmd_config_save(args: list[str], session: SessionState) -> None:
    """Persist session config overrides to config.yaml."""
    if not session.config_overrides:
        console.print("[dim]No session overrides to save.[/dim]")
        return

    saved = save_config_to_yaml(session)
    if saved:
        console.print(f"  [green]{saved} setting(s) saved to config.yaml[/green]")
        for key, value in session.config_overrides.items():
            console.print(f"    {key} = {value}")
        console.print("[dim]  Settings persist across restarts.[/dim]")
    else:
        print_error(
            UserError(
                detail="Failed to save config",
                reason="The config.yaml write operation failed.",
                solution="Check disk space and permissions for ~/.cra/config.yaml.",
            ),
            console=console,
        )


def save_config_to_yaml(session: SessionState) -> int:
    """Write config overrides to config.yaml.

    Skips API keys (those are managed in secrets.env).
    Returns the number of settings saved.
    """
    try:
        store = session._get_config_store()
        count = 0
        for key, value in session.config_overrides.items():
            if key.endswith("_api_key"):
                continue
            store.set_value(key, str(value))
            count += 1
        return count
    except Exception:
        return 0


def cmd_config_reset(args: list[str], session: SessionState) -> None:
    """Reload config from .env, discard overrides (preserves API keys).

    Use ``config factory-reset`` to also clear health marks and review history.
    """
    if args and args[0] == "factory-reset":
        return _cmd_factory_reset(session)

    count = len(session.config_overrides)
    session.config_overrides.clear()
    session.invalidate_settings_cache()

    # Clear persisted overrides from config.yaml, preserving health and state
    try:
        session._get_config_store().clear_overrides()
    except Exception as exc:
        logger.debug("failed to clear persisted config from config.yaml", error=str(exc))

    console.print(f"  [green]Reset {count} override(s). Config reloaded from .env.[/green]")
    console.print("  [dim]API keys and provider health preserved.[/dim]")


def _cmd_factory_reset(session: SessionState) -> None:
    """Full factory reset: clear all config, health marks, and review history.

    Preserves API keys (in secrets.env) so providers remain accessible.
    """
    from prompt_toolkit import prompt as pt_prompt

    console.print()
    console.print("  [bold red]Factory Reset[/bold red]")
    console.print()
    console.print("  [bold]Will clear:[/bold]")
    console.print("    [red]x[/red] All config overrides (config.yaml)")
    console.print("    [red]x[/red] All health marks (not working status)")
    console.print("    [red]x[/red] All review history and findings")
    console.print()
    console.print("  [bold]Preserved:[/bold]")
    console.print("    [green]>[/green] API keys for all providers (secrets.env)")
    console.print()

    try:
        answer = pt_prompt("  Type 'reset' to confirm: ").strip()
    except (KeyboardInterrupt, EOFError):
        console.print("  [dim]Cancelled.[/dim]")
        return

    if answer != "reset":
        console.print("  [dim]Cancelled.[/dim]")
        return

    session.config_overrides.clear()
    session.invalidate_settings_cache()

    try:
        # Clear config.yaml (overrides + health + state)
        config_store = session._get_config_store()
        config_store.save({})

        # Clear review history from DB
        from code_review_agent.storage import ReviewStorage

        storage = ReviewStorage(session.effective_settings.history_db_path)
        storage.clear_review_history()

        # Count preserved API keys
        secrets_store = session._get_secrets_store()
        key_count = len(secrets_store.load_all_keys())

        console.print(
            f"  [green]Factory reset complete."
            f" {key_count} API key(s) preserved in secrets.env.[/green]"
        )
    except Exception as exc:
        print_error(classify_exception(exc, context="Factory reset"), console=console)


def cmd_config_validate(args: list[str], session: SessionState) -> None:
    """Validate current config."""
    try:
        from code_review_agent.config import Settings

        Settings()
        console.print("[green]Config is valid.[/green]")
    except Exception as exc:
        print_error(classify_exception(exc, context="Config validation"), console=console)


def cmd_config_diff(args: list[str], session: SessionState) -> None:
    """Show differences between session and .env config."""
    if not session.config_overrides:
        console.print("[dim]No session overrides. Config matches .env.[/dim]")
        return
    for key, value in session.config_overrides.items():
        original = getattr(session.settings, key, None)
        display_new = "****" if key.endswith("_api_key") or key == "llm_api_key" else value
        console.print(f"  {key}: {_mask_secret(original)} -> {display_new}")
