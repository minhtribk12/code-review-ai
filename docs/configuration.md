# Configuration

## Loading Order

Settings are loaded with the following priority (highest wins):

1. **CLI flags** -- `--verbose`, `--output`, etc.
2. **Environment variables** -- `NVIDIA_API_KEY=...`
3. **`.env` file** -- loaded automatically by pydantic-settings
4. **Defaults** -- hardcoded in the `Settings` class

## Settings Reference

### LLM Settings

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `LLM_PROVIDER` | `KnownProvider` | `nvidia` | API provider: `nvidia`, `openrouter` |
| `NVIDIA_API_KEY` | `SecretStr \| None` | `None` | API key for NVIDIA NIM (required when provider is `nvidia`) |
| `OPENROUTER_API_KEY` | `SecretStr \| None` | `None` | API key for OpenRouter (required when provider is `openrouter`) |
| `LLM_MODEL` | `str` | `nvidia/nemotron-3-super-120b-a12b` | Model identifier passed to the provider |
| `LLM_BASE_URL` | `str \| None` | `None` | Custom API URL; overrides provider URL resolution |
| `LLM_TEMPERATURE` | `float` | `0.1` | Sampling temperature, range 0.0--1.0 (lower = more deterministic) |
| `REQUEST_TIMEOUT_SECONDS` | `int` | `120` | Per-request timeout in seconds (min: 1) |
| `TEST_CONNECTION_ON_START` | `bool` | `true` | Send a minimal 1-token request on startup and after LLM config changes to verify connectivity |

**Note:** In the interactive config editor (`config edit`), API keys are shown as a single `llm_api_key` field that automatically maps to the active provider's key. When you switch providers, the displayed key updates accordingly. Keys are stored per-provider in `~/.cra/secrets.env` and never appear in `config_overrides`.

### Token Budget

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `TOKEN_TIER` | `TokenTier` | `free` | Budget tier: `free` (5 000 tokens), `standard` (16 000), `premium` (48 000). Auto-detected from model name when possible; tier is the fallback. |
| `MAX_PROMPT_TOKENS` | `int \| None` | `None` | Explicit prompt token budget; overrides both tier and auto-detection |
| `MAX_TOKENS_PER_REVIEW` | `int \| None` | `None` | Hard cap on total tokens across all agents + synthesis per review. Logs a warning when exceeded. |
| `LLM_INPUT_PRICE_PER_M` | `float \| None` | `None` | Custom input price per 1M tokens for cost estimation. Must be set together with output price. |
| `LLM_OUTPUT_PRICE_PER_M` | `float \| None` | `None` | Custom output price per 1M tokens for cost estimation. Must be set together with input price. |

### Rate Limiting

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `RATE_LIMIT_RPM` | `int \| None` | `None` | Requests per minute. Auto-detected from tier by default (`free`=5, `standard`=30, `premium`=unlimited). Adapts automatically from provider 429 responses. |

### Review Settings

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `DEDUP_STRATEGY` | `DedupStrategy` | `exact` | Finding deduplication across agents: `exact` (file+line+title), `location` (file+line), `similar` (title similarity >0.6), `disabled` |
| `MAX_REVIEW_SECONDS` | `int` | `600` | Maximum wall-clock time for the full review (all agents + synthesis), in seconds (min: 10) |
| `MAX_CONCURRENT_AGENTS` | `int` | `4` | Maximum number of agents running in parallel (min: 1) |
| `DEFAULT_AGENTS` | `str` | `""` (empty) | Comma-separated list of agents to run by default. Empty string uses tier defaults. |

### Iterative Review

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `MAX_DEEPENING_ROUNDS` | `int` | `1` | Number of iterative deepening rounds; each round feeds previous findings back to agents. Cost multiplier = N. Range: 1--5. |
| `IS_VALIDATION_ENABLED` | `bool` | `false` | Enable a separate validator agent that reviews all findings for false positives. Adds 1 LLM call per validation round. |
| `MAX_VALIDATION_ROUNDS` | `int` | `1` | Maximum validation passes when validation is enabled. Range: 1--3. |

### GitHub

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `GITHUB_TOKEN` | `SecretStr \| None` | `None` | GitHub personal access token for PR integration (masked in logs) |
| `GITHUB_RATE_LIMIT_WARN_THRESHOLD` | `int` | `100` | Log a WARNING when remaining GitHub API quota drops below this value (min: 0) |
| `MAX_PR_FILES` | `int` | `200` | Maximum number of files to fetch from a GitHub PR via pagination (min: 1) |
| `PR_STALE_DAYS` | `int` | `7` | Days of inactivity before a PR is considered stale by the `pr stale` command (min: 1) |

### Custom Agents

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `CUSTOM_AGENTS_DIR` | `str` | `~/.cra/agents` | Directory containing YAML agent definitions. Project-local agents are also loaded from `.cra/agents/` in the repo root. |

### History

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `HISTORY_DB_PATH` | `str` | `~/.cra/reviews.db` | Path to the SQLite database for review history |
| `AUTO_SAVE_HISTORY` | `bool` | `true` | Automatically persist every review to the history database |

### Interactive

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `INTERACTIVE_HISTORY_FILE` | `str` | `~/.cra_history` | Path to the readline history file for the interactive shell |
| `INTERACTIVE_PROMPT` | `str` | `cra> ` | Prompt string displayed in the interactive shell |
| `INTERACTIVE_VI_MODE` | `bool` | `false` | Enable vi keybindings in the interactive shell |
| `INTERACTIVE_AUTOCOMPLETE_CACHE_TTL` | `int` | `5` | Autocomplete cache lifetime in seconds (min: 1) |

### Watch

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `WATCH_DEBOUNCE_SECONDS` | `float` | `5.0` | Debounce interval in seconds for the `watch` command's file-change polling (min: 1.0) |

### Logging

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `LOG_LEVEL` | `LogLevel` | `INFO` | Application log level: `DEBUG`, `INFO`, `WARNING`, `ERROR`. Overridden by `--verbose` CLI flag. |

## Provider URL Resolution

The `resolved_llm_base_url` computed field determines the final API URL:

1. If `LLM_BASE_URL` is set, use it directly (escape hatch for local servers or custom endpoints).
2. Otherwise, look up `LLM_PROVIDER` in the provider registry:
   - `nvidia` -- `https://integrate.api.nvidia.com/v1`
   - `openrouter` -- `https://openrouter.ai/api/v1`
   - Custom providers -- URL from `~/.cra/providers.yaml`

### Provider Registry

Provider metadata (base URLs, models, rate limits) is stored in YAML:

- **Bundled defaults:** `<package>/provider_registry.yaml` (ships with install)
- **User overrides:** `~/.cra/providers.yaml` (add custom providers or extend existing ones)

Use `provider add` in interactive mode or edit `~/.cra/providers.yaml` directly. User-defined providers are merged on top of bundled defaults.

### API Key Resolution

API keys are resolved in this order:

1. **Built-in provider fields** -- `NVIDIA_API_KEY` or `OPENROUTER_API_KEY` environment variables or `.env` entries
2. **Environment variable** -- `{PROVIDER}_API_KEY` for any provider (e.g., `OLLAMA_API_KEY`)
3. **Secrets file** -- Keys entered via the startup panel, `provider add`, or `config edit` are stored in `~/.cra/secrets.env`

Local LLM servers (URLs matching `localhost`, `127.x`, `10.x`, `172.16-31.x`, `192.168.x`) are auto-detected and do not require API keys.

## Secrets Handling

- `NVIDIA_API_KEY`, `OPENROUTER_API_KEY`, and `GITHUB_TOKEN` use Pydantic's `SecretStr` type.
- `print(settings.nvidia_api_key)` outputs `**********`.
- The raw value is accessed only at the HTTP boundary via `settings.resolved_api_key.get_secret_value()`.
- Secret values are never logged in prompts, responses, or error messages.

## Validation Rules

All settings are validated at startup. Invalid values produce a `ValidationError` immediately, not at first use.

- The active provider must exist in the provider registry (bundled or user-defined).
- `LLM_PROVIDER` must be one of: `nvidia`, `openrouter`.
- The active provider must have its API key set (`NVIDIA_API_KEY` for nvidia, `OPENROUTER_API_KEY` for openrouter).
- `LLM_TEMPERATURE` must be between 0.0 and 1.0 inclusive.
- `REQUEST_TIMEOUT_SECONDS` must be >= 1.
- `MAX_REVIEW_SECONDS` must be >= 10.
- `MAX_CONCURRENT_AGENTS` must be >= 1.
- `MAX_PR_FILES` must be >= 1.
- `GITHUB_RATE_LIMIT_WARN_THRESHOLD` must be >= 0.
- `PR_STALE_DAYS` must be >= 1.
- `MAX_DEEPENING_ROUNDS` must be between 1 and 5 inclusive.
- `MAX_VALIDATION_ROUNDS` must be between 1 and 3 inclusive.
- `INTERACTIVE_AUTOCOMPLETE_CACHE_TTL` must be >= 1.
- `WATCH_DEBOUNCE_SECONDS` must be >= 1.0.
- `LOG_LEVEL` must be one of: `DEBUG`, `INFO`, `WARNING`, `ERROR`.
- `LLM_INPUT_PRICE_PER_M` and `LLM_OUTPUT_PRICE_PER_M` must both be set or both be unset. Setting only one raises a `ValueError`.

## Reset and Factory Reset

### `config reset`

Clears all session overrides and persisted config from `~/.cra/config.yaml`, then reloads from `.env`. Preserves:
- API keys for all providers
- Provider health marks

### `config factory-reset`

Full wipe requiring confirmation (type "reset" to confirm). Clears:
- All config overrides (session + database)
- All health marks (not working status)
- All review history, findings, and agent results

Preserves:
- API keys for all providers

This is useful when the database has accumulated stale data or you want a clean start without re-entering API keys.

## Example `.env`

```env
# -- LLM --
LLM_PROVIDER=nvidia
NVIDIA_API_KEY=your-nvidia-api-key-here
# OPENROUTER_API_KEY=your-openrouter-api-key-here
LLM_MODEL=nvidia/nemotron-3-super-120b-a12b
# LLM_BASE_URL=http://localhost:8000/v1
LLM_TEMPERATURE=0.1
REQUEST_TIMEOUT_SECONDS=120
TEST_CONNECTION_ON_START=true

# -- Token budget --
TOKEN_TIER=free
# MAX_PROMPT_TOKENS=32000
# MAX_TOKENS_PER_REVIEW=50000
# LLM_INPUT_PRICE_PER_M=0.30
# LLM_OUTPUT_PRICE_PER_M=0.60

# -- Rate limiting --
# RATE_LIMIT_RPM=10

# -- Review --
DEDUP_STRATEGY=exact
MAX_REVIEW_SECONDS=600
MAX_CONCURRENT_AGENTS=4
# DEFAULT_AGENTS=security,performance

# -- Iterative review --
# MAX_DEEPENING_ROUNDS=1
# IS_VALIDATION_ENABLED=false
# MAX_VALIDATION_ROUNDS=1

# -- GitHub --
GITHUB_TOKEN=ghp_your_token_here
# GITHUB_RATE_LIMIT_WARN_THRESHOLD=100
MAX_PR_FILES=200
# PR_STALE_DAYS=7

# -- Custom agents --
# CUSTOM_AGENTS_DIR=~/.cra/agents

# -- History --
# HISTORY_DB_PATH=~/.cra/reviews.db
# AUTO_SAVE_HISTORY=true

# -- Interactive --
# INTERACTIVE_HISTORY_FILE=~/.cra_history
# INTERACTIVE_PROMPT=cra>
# INTERACTIVE_VI_MODE=false
# INTERACTIVE_AUTOCOMPLETE_CACHE_TTL=5

# -- Watch --
# WATCH_DEBOUNCE_SECONDS=5.0

# -- Logging --
LOG_LEVEL=INFO
```
