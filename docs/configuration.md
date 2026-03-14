# Configuration

## Loading Order

Settings are loaded with the following priority (highest wins):

1. **CLI flags** -- `--verbose`, `--output`, etc.
2. **Environment variables** -- `LLM_API_KEY=...`
3. **`.env` file** -- loaded automatically by pydantic-settings
4. **Defaults** -- hardcoded in the `Settings` class

## Settings Reference

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `LLM_PROVIDER` | `KnownProvider` | `openrouter` | API provider (`openrouter`, `nvidia`, `openai`) |
| `LLM_API_KEY` | `SecretStr` | *required* | API key (masked in logs) |
| `LLM_MODEL` | `str` | `nvidia/nemotron-3-super-120b-a12b` | Model ID |
| `LLM_BASE_URL` | `str \| None` | `None` | Custom API URL (overrides provider URL) |
| `LLM_TEMPERATURE` | `float` | `0.1` | Response temperature, range 0.0-1.0 |
| `REQUEST_TIMEOUT_SECONDS` | `int` | `120` | Per-request timeout (min: 1) |
| `GITHUB_TOKEN` | `SecretStr \| None` | `None` | GitHub PAT for private repos |
| `LOG_LEVEL` | `LogLevel` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `MAX_CONCURRENT_AGENTS` | `int` | `4` | Max parallel agent threads |

## Provider URL Resolution

The `resolved_llm_base_url` computed field resolves the final API URL:

1. If `LLM_BASE_URL` is set, use it directly (escape hatch for local servers)
2. Otherwise, map `LLM_PROVIDER` to a known URL:
   - `openrouter` -> `https://openrouter.ai/api/v1`
   - `nvidia` -> `https://integrate.api.nvidia.com/v1`
   - `openai` -> `https://api.openai.com/v1`

## Secrets Handling

- `LLM_API_KEY` and `GITHUB_TOKEN` use `SecretStr`
- `print(settings.llm_api_key)` shows `**********`
- Raw value only accessed at the boundary: `settings.llm_api_key.get_secret_value()`
- Never logged in prompts, responses, or error messages

## Validation

All settings are validated at startup:

- `LLM_PROVIDER` must be one of the `KnownProvider` enum values
- `LLM_TEMPERATURE` must be between 0.0 and 1.0
- `REQUEST_TIMEOUT_SECONDS` must be >= 1
- `LOG_LEVEL` must be one of the `LogLevel` enum values
- Invalid values produce a `ValidationError` at startup, not at first use

## Example `.env`

```env
LLM_PROVIDER=openrouter
LLM_API_KEY=your-api-key-here
LLM_MODEL=nvidia/nemotron-3-super-120b-a12b
LLM_TEMPERATURE=0.1
REQUEST_TIMEOUT_SECONDS=120
GITHUB_TOKEN=ghp_your_token_here
LOG_LEVEL=INFO
MAX_CONCURRENT_AGENTS=4
```
