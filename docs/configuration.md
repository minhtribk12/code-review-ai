# Configuration

## Loading Order

Settings are loaded with the following priority (highest wins):

1. **CLI flags** -- `--verbose`, `--output`, etc.
2. **Environment variables** -- `LLM_API_KEY=...`
3. **`.env` file** -- loaded automatically by pydantic-settings
4. **Defaults** -- hardcoded in the `Settings` class

## Settings Reference

### LLM Settings

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `LLM_PROVIDER` | `KnownProvider` | `openrouter` | API provider: `openrouter`, `nvidia`, `openai` |
| `LLM_API_KEY` | `SecretStr` | *required* | API key for the LLM provider (masked in logs) |
| `LLM_MODEL` | `str` | `nvidia/nemotron-3-super-120b-a12b` | Model identifier passed to the provider |
| `LLM_BASE_URL` | `str \| None` | `None` | Custom API URL; overrides provider URL resolution |
| `LLM_TEMPERATURE` | `float` | `0.1` | Sampling temperature, range 0.0--1.0 (lower = more deterministic) |
| `REQUEST_TIMEOUT_SECONDS` | `int` | `120` | Per-request timeout in seconds (min: 1) |

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
2. Otherwise, map `LLM_PROVIDER` to a known URL:
   - `openrouter` -- `https://openrouter.ai/api/v1`
   - `nvidia` -- `https://integrate.api.nvidia.com/v1`
   - `openai` -- `https://api.openai.com/v1`

## Secrets Handling

- `LLM_API_KEY` and `GITHUB_TOKEN` use Pydantic's `SecretStr` type.
- `print(settings.llm_api_key)` outputs `**********`.
- The raw value is accessed only at the HTTP boundary via `settings.llm_api_key.get_secret_value()`.
- Secret values are never logged in prompts, responses, or error messages.

## Validation Rules

All settings are validated at startup. Invalid values produce a `ValidationError` immediately, not at first use.

- `LLM_PROVIDER` must be one of: `openrouter`, `nvidia`, `openai`.
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

## Example `.env`

```env
# -- LLM --
LLM_PROVIDER=openrouter
LLM_API_KEY=your-api-key-here
LLM_MODEL=nvidia/nemotron-3-super-120b-a12b
# LLM_BASE_URL=http://localhost:8000/v1
LLM_TEMPERATURE=0.1
REQUEST_TIMEOUT_SECONDS=120

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
