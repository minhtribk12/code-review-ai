"""Provider management commands: add, list, remove custom LLM providers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml
from prompt_toolkit import prompt as pt_prompt
from rich.console import Console
from rich.table import Table

import code_review_agent.providers as _providers_mod
from code_review_agent.providers import (
    _USER_REGISTRY_PATH,
    get_provider,
    reload_registry,
)
from code_review_agent.theme import theme

if TYPE_CHECKING:
    from code_review_agent.interactive.session import SessionState

console = Console()

_ABORT_HINT = "[dim](Ctrl+C to abort)[/dim]"


class _AbortWizard(Exception):
    """Raised when user aborts the provider add wizard."""


def _prompt(
    label: str,
    *,
    default: str = "",
    required: bool = False,
    validate_int: bool = False,
    validate_url: bool = False,
) -> str:
    """Prompt user for input with paste support.

    Raises _AbortWizard on Ctrl+C / EOFError.
    Validates immediately and re-prompts on invalid input.
    """
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            result = pt_prompt(f"  {label}{suffix}: ").strip()
        except (KeyboardInterrupt, EOFError):
            raise _AbortWizard from None

        value = result if result else default

        if required and not value:
            console.print("    [red]Required. Enter a value or Ctrl+C to abort.[/red]")
            continue

        if validate_int and value:
            try:
                int(value)
            except ValueError:
                console.print(f"    [red]Must be a number, got: {value}[/red]")
                continue

        if validate_url and value and not value.startswith(("http://", "https://")):
            console.print(f"    [red]Must start with http:// or https://, got: {value}[/red]")
            continue

        return value


def _confirm(label: str, *, default_yes: bool = True) -> bool:
    """Yes/no confirmation prompt. Raises _AbortWizard on Ctrl+C."""
    hint = "Y/n" if default_yes else "y/N"
    try:
        result = pt_prompt(f"  {label} ({hint}): ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        raise _AbortWizard from None

    if not result:
        return default_yes
    return result in ("y", "yes")


def _load_user_registry() -> dict[str, Any]:
    """Load user providers.yaml or return empty structure."""
    if _USER_REGISTRY_PATH.is_file():
        raw = yaml.safe_load(_USER_REGISTRY_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        result: dict[str, Any] = raw.get("providers", {})
        return result
    return {}


def _save_user_registry(providers: dict[str, Any]) -> None:
    """Save providers dict to user providers.yaml."""
    _USER_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _USER_REGISTRY_PATH.write_text(
        yaml.safe_dump(
            {"providers": providers},
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


def cmd_provider(args: list[str], session: SessionState) -> None:
    """Provider management: browse, add, list, remove."""
    if not args:
        from code_review_agent.interactive.commands.provider_browser import (
            run_provider_browser,
        )

        return run_provider_browser(session)

    sub = args[0]
    if sub == "add":
        return cmd_provider_add(args[1:], session)
    if sub in ("list", "ls"):
        return cmd_provider_list(args[1:], session)
    if sub in ("remove", "rm"):
        return cmd_provider_remove(args[1:], session)
    if sub == "models":
        return cmd_provider_models(args[1:], session)

    console.print(f"[red]Unknown subcommand: {sub}[/red]")
    console.print("Usage: provider [add|list|remove|models]")


def cmd_provider_add(args: list[str], session: SessionState) -> None:
    """Interactive wizard to add a custom provider or models to existing one."""
    console.print(f"\n  [{theme.info}]Add LLM Provider[/{theme.info}]  {_ABORT_HINT}")
    console.print("  Paste or type values. Enter accepts defaults.\n")

    try:
        _run_add_wizard(session)
    except _AbortWizard:
        console.print(f"\n  [{theme.warning}]Aborted. No changes saved.[/{theme.warning}]")


def _run_add_wizard(session: SessionState) -> None:
    """Core wizard logic. Raises _AbortWizard on user abort."""
    # --- Step 1: Provider name ---
    name = _prompt("Provider name (e.g. ollama, together)", required=True)

    is_existing = name in _providers_mod.PROVIDER_REGISTRY
    if is_existing:
        existing = get_provider(name)
        console.print(
            f"  [{theme.warning}]Provider '{name}' exists. Adding models to it.[/{theme.warning}]"
        )
        default_url = existing.base_url
        default_rpm = str(existing.rate_limit_rpm)
    else:
        default_url = ""
        default_rpm = "10"

    # --- Step 2: Base URL ---
    base_url = _prompt(
        "Base URL (e.g. https://api.example.com/v1)",
        default=default_url,
        required=not is_existing,
        validate_url=True,
    )

    # --- Step 3: API key ---
    api_key = _prompt("API key (paste your key, Enter to skip)")
    api_key_env = f"{name.upper()}_API_KEY"

    # --- Step 4: Rate limit (skippable with default) ---
    rpm_str = _prompt(
        "Rate limit requests/min (Enter to skip)",
        default=default_rpm,
        validate_int=True,
    )
    rpm = int(rpm_str) if rpm_str else 10

    # --- Step 5: Models ---
    console.print(f"\n  [{theme.info}]Add models[/{theme.info}]  {_ABORT_HINT}")
    console.print("  [dim]Model name = the exact API identifier sent to the LLM server.[/dim]")
    console.print("  [dim]Label = display name shown in selectors (must also be unique).[/dim]")
    models: list[dict[str, Any]] = []

    # Collect existing names and labels to prevent duplicates (per-provider)
    existing_model_names: set[str] = set()
    existing_model_labels: set[str] = set()
    if is_existing:
        for existing_m in get_provider(name).models:
            existing_model_names.add(existing_m.id)
            existing_model_labels.add(existing_m.name)
        if existing_model_names:
            console.print(
                f"  [dim]Existing models: {', '.join(sorted(existing_model_names))}[/dim]"
            )

    new_model_names: set[str] = set()
    new_model_labels: set[str] = set()

    while True:
        console.print(f"\n  --- Model #{len(models) + 1} ---")

        # Validate model name (API identifier): required, unique per provider
        while True:
            model_name = _prompt(
                "Model name (API identifier, e.g. meta-llama/llama-3.1-8b)",
                required=True,
            )
            if model_name in new_model_names:
                console.print(f"    [red]Duplicate: '{model_name}' already added above.[/red]")
                continue
            if model_name in existing_model_names:
                console.print(
                    f"    [red]Duplicate: '{model_name}' already exists in this provider.[/red]"
                )
                continue
            break

        # Validate label (display name): unique per provider
        default_label = model_name.split("/")[-1]
        while True:
            model_label = _prompt("Label (display name)", default=default_label)
            if model_label in new_model_labels:
                console.print(
                    f"    [red]Duplicate label: '{model_label}' already used above.[/red]"
                )
                continue
            if model_label in existing_model_labels:
                console.print(
                    f"    [red]Duplicate label: '{model_label}' already exists "
                    f"in this provider.[/red]"
                )
                continue
            break

        # Validate is_free: must be yes/no/true/false
        while True:
            is_free_str = _prompt("Is free? (yes/no)", default="yes")
            if is_free_str.lower() in ("yes", "y", "true", "1"):
                is_free = True
                break
            if is_free_str.lower() in ("no", "n", "false", "0"):
                is_free = False
                break
            console.print("    [red]Must be yes or no.[/red]")

        ctx_str = _prompt(
            "Context window tokens (Enter for 128000)",
            default="128000",
            validate_int=True,
        )
        ctx = int(ctx_str) if ctx_str else 128_000

        models.append(
            {
                "id": model_name,
                "name": model_label,
                "is_free": is_free,
                "context_window": ctx,
            }
        )
        new_model_names.add(model_name)
        new_model_labels.add(model_label)
        console.print(f"    [green]Added: {model_name} ({model_label})[/green]")

        if not _confirm("Add another model?", default_yes=False):
            break

    if not models and not is_existing:
        console.print("[red]  At least one model is required for a new provider.[/red]")
        return

    # --- Step 6: Default model ---
    default_model = ""
    if models:
        model_ids = [str(m["id"]) for m in models]
        if len(model_ids) == 1:
            default_model = model_ids[0]
        else:
            console.print(f"\n  Available: {', '.join(model_ids)}")
            default_model = _prompt("Default model", default=model_ids[0])

    # --- Summary & confirmation ---
    console.print(f"\n  [{theme.info}]Summary:[/{theme.info}]")
    console.print(f"    Provider:      {name}")
    console.print(f"    Base URL:      {base_url}")
    console.print(f"    API key:       {'****' if api_key else 'not set'}")
    console.print(f"    Rate limit:    {rpm} rpm")
    if default_model:
        console.print(f"    Default model: {default_model}")
    for m in models:
        free_tag = " (free)" if m["is_free"] else ""
        console.print(
            f"    Model:         {m['id']} ({m['name']}){free_tag}  [{m['context_window']:,} ctx]"
        )

    if not _confirm("\n  Save this provider?"):
        console.print(f"  [{theme.warning}]Discarded. No changes saved.[/{theme.warning}]")
        return

    # --- Save ---
    user_providers = _load_user_registry()

    provider_entry: dict[str, Any] = {
        "base_url": base_url,
        "rate_limit_rpm": rpm,
    }
    if default_model:
        provider_entry["default_model"] = default_model
    if models:
        existing_models: list[dict[str, Any]] = []
        if name in user_providers and isinstance(user_providers[name], dict):
            existing_models = user_providers[name].get("models", [])
        existing_ids = {m["id"] for m in existing_models}
        new_models = [m for m in models if m["id"] not in existing_ids]
        provider_entry["models"] = existing_models + new_models

    user_providers[name] = provider_entry
    _save_user_registry(user_providers)
    reload_registry()

    # Save API key to secrets.env (not in config_overrides -- keys are secret)
    if api_key:
        session.save_api_key(name, api_key)

    console.print(
        f"\n  [{theme.success}]Provider '{name}' saved to {_USER_REGISTRY_PATH}[/{theme.success}]"
    )
    if models:
        console.print(f"  [{theme.success}]{len(models)} model(s) added[/{theme.success}]")
    if api_key:
        console.print(f"  [{theme.success}]API key saved[/{theme.success}]")
    else:
        console.print(f"  [dim]No API key set. Use: export {api_key_env}=your-key[/dim]")

    # Test connection with the newly added provider
    if api_key and session.effective_settings.test_connection_on_start:
        # Temporarily switch to the new provider to test it
        prev_config = {
            k: session.config_overrides.get(k, str(getattr(session.settings, k, "") or ""))
            for k in ("llm_provider", "llm_model", "llm_base_url")
        }
        session.config_overrides["llm_provider"] = name
        session.config_overrides["llm_model"] = default_model or str(models[0]["id"])
        session.config_overrides["llm_base_url"] = base_url
        session.invalidate_settings_cache()

        from code_review_agent.interactive.repl import run_connection_test

        run_connection_test(session, previous_llm_config=prev_config)


def cmd_provider_list(args: list[str], session: SessionState) -> None:
    """List all registered providers and their models."""
    table = Table(title="LLM Providers", show_lines=True)
    table.add_column("Provider", style="bold")
    table.add_column("Base URL")
    table.add_column("RPM", justify="right")
    table.add_column("Default Model")
    table.add_column("Models", justify="right")

    user_providers = _load_user_registry()

    for key, info in sorted(_providers_mod.PROVIDER_REGISTRY.items()):
        source = " [dim](custom)[/dim]" if key in user_providers else ""
        free_count = len(info.free_models)
        total = len(info.models)
        model_label = f"{free_count} free / {total} total"
        table.add_row(
            f"{key}{source}",
            info.base_url,
            str(info.rate_limit_rpm),
            info.default_model,
            model_label,
        )

    console.print(table)
    console.print(f"\n  [dim]User providers: {_USER_REGISTRY_PATH}[/dim]")


def cmd_provider_models(args: list[str], session: SessionState) -> None:
    """List models for a specific provider."""
    if not args:
        console.print("[red]Usage: provider models <name>[/red]")
        return

    name = args[0]
    try:
        info = get_provider(name)
    except KeyError:
        console.print(f"[red]Unknown provider: {name}[/red]")
        return

    table = Table(title=f"Models: {name}", show_lines=False)
    table.add_column("Model ID", style="bold")
    table.add_column("Name")
    table.add_column("Free", justify="center")
    table.add_column("Context", justify="right")
    table.add_column("", style="dim")

    for m in info.models:
        is_default = " (default)" if m.id == info.default_model else ""
        free_label = "[green]yes[/green]" if m.is_free else "[red]no[/red]"
        ctx_label = f"{m.context_window:,}"
        table.add_row(m.id, m.name, free_label, ctx_label, is_default)

    console.print(table)


def cmd_provider_remove(args: list[str], session: SessionState) -> None:
    """Remove a user-defined provider (cannot remove bundled providers)."""
    if not args:
        console.print("[red]Usage: provider remove <name>[/red]")
        return

    name = args[0]
    user_providers = _load_user_registry()

    if name not in user_providers:
        console.print(
            f"[red]'{name}' is not a user-defined provider "
            f"(only custom providers can be removed)[/red]"
        )
        return

    del user_providers[name]
    _save_user_registry(user_providers)
    reload_registry()

    console.print(f"  [{theme.success}]Provider '{name}' removed[/{theme.success}]")
