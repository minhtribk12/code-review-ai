# Setup Guide: External Services & LLM Provider Configuration

This guide covers connecting the code-review-ai to remote LLM servers, GitHub, and configuring all external service integrations.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration File (.env)](#configuration-file-env)
- [LLM Provider Setup](#llm-provider-setup)
  - [OpenRouter](#openrouter)
  - [NVIDIA](#nvidia)
  - [Azure OpenAI](#azure-openai)
  - [Self-hosted / Local Models](#self-hosted--local-models)
- [GitHub Integration](#github-integration)
- [Token Budget & Cost Control](#token-budget--cost-control)
- [Rate Limiting](#rate-limiting)
- [Verification](#verification)
- [Troubleshooting](#troubleshooting)
- [Connection Test](#connection-test)

---

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- Git (for PR integration and interactive mode)

## Installation

```bash
git clone https://github.com/minhtribk12/code-review-ai.git
cd code-review-ai
make install
```

## Configuration File (.env)

All configuration is done through environment variables. The easiest way is to create a `.env` file in the project root:

```bash
cp .env.example .env
```

**Loading priority** (highest wins):
1. CLI flags (`--verbose`, `--output`, etc.)
2. Shell environment variables (`export NVIDIA_API_KEY=...`)
3. `.env` file in the project root
4. Hardcoded defaults

The `.env` file is automatically loaded by pydantic-settings at startup. You can also export variables directly in your shell or pass them inline:

```bash
# Inline
NVIDIA_API_KEY=nvapi-... uv run cra review --diff file.patch

# Or export
export NVIDIA_API_KEY=nvapi-...
uv run cra review --diff file.patch
```

---

## LLM Provider Setup

The agent uses the OpenAI Python SDK as its HTTP transport. This means it works with **any API server that implements the OpenAI chat completions endpoint** (`/v1/chat/completions`). Two providers (NVIDIA and OpenRouter) have built-in URL mappings with free models. Additional providers can be added via the `provider add` command or by creating `~/.cra/providers.json`. Any OpenAI-compatible server is supported via `LLM_BASE_URL`.

### How Provider URL Resolution Works

1. If `LLM_BASE_URL` is set, it is used directly (ignores `LLM_PROVIDER`)
2. Otherwise, `LLM_PROVIDER` maps to a URL from the provider registry:
   - `nvidia` -> `https://integrate.api.nvidia.com/v1`
   - `openrouter` -> `https://openrouter.ai/api/v1`
   - Custom providers -> URL from `~/.cra/providers.json`

---

### OpenRouter

[OpenRouter](https://openrouter.ai/) is a unified gateway to 100+ models (Llama, Mistral, GPT, Claude, Gemini, etc.) with a single API key.

**Step 1: Get an API key**
1. Sign up at https://openrouter.ai/
2. Go to https://openrouter.ai/keys
3. Create a new API key

**Step 2: Configure `.env`**
```env
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=your-openrouter-api-key-here
LLM_MODEL=openrouter/auto
```

**Available models** (examples -- check OpenRouter for current list):
```env
# NVIDIA (cost-effective, large context)
LLM_MODEL=nvidia/nemotron-3-super-120b-a12b

# Meta Llama
LLM_MODEL=meta-llama/llama-3.1-70b-instruct
LLM_MODEL=meta-llama/llama-3.1-8b-instruct

# Mistral
LLM_MODEL=mistralai/mistral-7b-instruct
LLM_MODEL=mistralai/mixtral-8x7b-instruct

# Google
LLM_MODEL=google/gemma-2-9b-it
```

**Note:** When using OpenRouter, the model name must match OpenRouter's naming convention (e.g., `nvidia/nemotron-3-super-120b-a12b`, not just `nemotron`). Check https://openrouter.ai/models for exact identifiers.

---

### NVIDIA

NVIDIA provides free-tier API access to select models.

**Step 1: Get an API key**
1. Sign up at https://build.nvidia.com/
2. Navigate to the model you want to use
3. Click "Get API Key"

**Step 2: Configure `.env`**
```env
LLM_PROVIDER=nvidia
NVIDIA_API_KEY=nvapi-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
LLM_MODEL=nvidia/nemotron-3-super-120b-a12b
```

**Available models:**
```env
LLM_MODEL=nvidia/nemotron-3-super-120b-a12b        # 1M context, free
LLM_MODEL=nvidia/nemotron-3-nano-30b-a3b            # 1M context, free
LLM_MODEL=nvidia/llama-3.3-nemotron-super-49b-v1.5  # 131k context, free
LLM_MODEL=nvidia/llama-3.1-nemotron-70b-instruct    # 131k context, free
```

---

### Self-hosted / Local Models

Any server implementing the OpenAI-compatible `/v1/chat/completions` endpoint works. The recommended approach is to register it as a custom provider using `provider add` in interactive mode. Alternatively, use `LLM_BASE_URL` to point to it directly.

#### Ollama

```bash
# Start Ollama
ollama serve
ollama pull llama3.1
```

```env
LLM_BASE_URL=http://localhost:11434/v1
NVIDIA_API_KEY=ollama
LLM_MODEL=llama3.1
```

**Note:** Ollama does not authenticate, but the API key field must be non-empty. Use any placeholder value.

#### vLLM

```bash
# Start vLLM server
python -m vllm.entrypoints.openai.api_server \
  --model meta-llama/Llama-3.1-8B-Instruct \
  --port 8000
```

```env
LLM_BASE_URL=http://localhost:8000/v1
NVIDIA_API_KEY=vllm
LLM_MODEL=meta-llama/Llama-3.1-8B-Instruct
```

#### llama.cpp Server

```bash
./llama-server -m model.gguf --port 8080
```

```env
LLM_BASE_URL=http://localhost:8080/v1
NVIDIA_API_KEY=llamacpp
LLM_MODEL=local-model
```

#### LM Studio

1. Start LM Studio and load a model
2. Enable the local server (default port: 1234)

```env
LLM_BASE_URL=http://localhost:1234/v1
NVIDIA_API_KEY=lmstudio
LLM_MODEL=local-model
```

#### Remote Self-hosted Server

For a model running on a remote machine (e.g., a GPU server):

```env
LLM_BASE_URL=https://your-server.example.com/v1
NVIDIA_API_KEY=your-server-api-key
LLM_MODEL=your-model-name
```

**Key points for all self-hosted setups:**
- `LLM_BASE_URL` must end with `/v1` (the SDK appends `/chat/completions`)
- An API key must be non-empty (use any placeholder if the server has no auth)
- `LLM_MODEL` must match the model name the server expects
- Increase `REQUEST_TIMEOUT_SECONDS` for slower local hardware (e.g., `300`)
- Set `TOKEN_TIER=free` and `RATE_LIMIT_RPM=0` (unlimited) for local servers

**Tip:** You can register self-hosted servers as custom providers using `provider add` in the interactive REPL. This lets you switch between them with `Ctrl+P` and tracks their models in `~/.cra/providers.json`.

---

## GitHub Integration

GitHub integration enables reviewing PRs directly and posting findings as PR comments.

### Step 1: Create a Personal Access Token

1. Go to https://github.com/settings/tokens
2. Click "Generate new token (classic)" or use fine-grained tokens
3. Required scopes:
   - **`repo`** -- read access to private repos, PR diffs, and PR metadata
   - **`write:discussion`** -- post review comments (only needed if using `pr approve` or findings posting)

For fine-grained tokens, grant:
- **Repository access:** Select the repos you want to review
- **Permissions:** Pull requests (Read & Write), Contents (Read)

### Step 2: Configure `.env`

```env
GITHUB_TOKEN=your-github-token-here
```

### Step 3: Usage

```bash
# Review a PR by reference
uv run cra review --pr owner/repo#123

# Review by full URL
uv run cra review --pr https://github.com/owner/repo/pull/123

# Interactive mode -- PR commands
uv run cra interactive
cra> pr list
cra> pr review 42
cra> pr diff 42
```

### GitHub Enterprise

For GitHub Enterprise Server, set the API base URL:

```env
GITHUB_TOKEN=your-github-token-here
GITHUB_API_BASE_URL=https://github.your-company.com/api/v3
```

### Rate Limits

- **Unauthenticated:** 60 requests/hour
- **Authenticated:** 5,000 requests/hour
- The agent tracks remaining quota from response headers
- Set `GITHUB_RATE_LIMIT_WARN_THRESHOLD` to get warnings before exhaustion

```env
GITHUB_RATE_LIMIT_WARN_THRESHOLD=100
```

---

## Token Budget & Cost Control

### Token Tiers

The agent enforces a token budget to control costs. Choose a tier based on your model's context window and API plan:

| Tier | Prompt Budget | Context Window | Default Agents | Best For |
|------|--------------|----------------|----------------|----------|
| `free` | 5,000 tokens | 8k models | security only | Free-tier APIs, small diffs |
| `standard` | 16,000 tokens | 32k models | all 4 built-in | Most reviews |
| `premium` | 48,000 tokens | 128k models | all 4 built-in | Large PRs, deep analysis |

```env
TOKEN_TIER=standard
```

### Budget Auto-detection

If the model is in the known registry, the budget is auto-calculated as **40% of the model's context window**. This overrides the tier setting. Override with:

```env
MAX_PROMPT_TOKENS=32000
```

### Cost Estimation

The agent estimates costs based on built-in pricing data. For models not in the registry (custom/local), set pricing manually:

```env
# Per 1M tokens (must set both or neither)
LLM_INPUT_PRICE_PER_M=0.30
LLM_OUTPUT_PRICE_PER_M=0.60
```

### Hard Token Cap

Limit total tokens across all agents + synthesis per review:

```env
MAX_TOKENS_PER_REVIEW=50000
```

---

## Rate Limiting

Rate limiting prevents 429 errors from the LLM provider.

| Tier | Requests/Minute |
|------|-----------------|
| `free` | 5 RPM |
| `standard` | 30 RPM |
| `premium` | unlimited |

Auto-detected from `TOKEN_TIER`. Override explicitly:

```env
RATE_LIMIT_RPM=10
```

Set to `0` for unlimited (local servers):

```env
RATE_LIMIT_RPM=0
```

The rate limiter also adapts dynamically: when the provider returns a 429 with a `retry-after` header, the agent adjusts its request rate accordingly.

---

## Verification

After configuring, verify your setup:

```bash
# Check configuration loads correctly
uv run cra config

# Run a quick review on a small diff
echo "--- a/test.py\n+++ b/test.py\n@@ -1 +1 @@\n-x = eval(input())\n+x = int(input())" > /tmp/test.patch
uv run cra review --diff /tmp/test.patch

# Check GitHub connection (if configured)
uv run cra interactive
cra> pr list
```

---

## Troubleshooting

### "API key is required"

You have not set an API key. Add it to `.env` or export it:
```bash
export NVIDIA_API_KEY=your-key-here  # or OPENROUTER_API_KEY
```

### Connection refused / timeout

- Verify the LLM server is running and reachable
- Check `LLM_BASE_URL` is correct (must end with `/v1`)
- Increase timeout: `REQUEST_TIMEOUT_SECONDS=300`
- For local servers, ensure the port is not blocked by a firewall

### 401 Unauthorized

- API key is invalid or expired
- For OpenRouter: check the key at https://openrouter.ai/keys
- For NVIDIA: check at https://build.nvidia.com/
- Ensure no extra whitespace in the key

### 429 Rate Limit

- Lower `RATE_LIMIT_RPM` to match your provider's limit
- Use `TOKEN_TIER=free` for free-tier APIs (auto-sets 5 RPM)
- The agent adapts automatically from 429 responses, but starting with a conservative RPM avoids the initial burst

### Model not found

- Verify the exact model identifier with your provider
- OpenRouter uses `provider/model-name` format (e.g., `nvidia/nemotron-3-super-120b-a12b`)
- NVIDIA uses `nvidia/model-name` format (e.g., `nvidia/nemotron-3-super-120b-a12b`)
- For local servers, check what model name the server reports

### GitHub PR fetch fails

- Verify `GITHUB_TOKEN` has `repo` scope
- For private repos, the token must have access to that specific repo
- Check rate limits: `gh api rate_limit` or look at the agent's warning logs

### Empty or unparseable LLM responses

- Some smaller models struggle with structured JSON output
- Try a larger model (70B+ recommended for reliable results)
- Lower temperature: `LLM_TEMPERATURE=0.0`
- Enable debug logging: `LOG_LEVEL=DEBUG` to see raw responses

### Docker

When running in Docker, pass configuration via environment variables:

```bash
docker build -t code-review-ai:latest .

docker run \
  -e LLM_API_KEY=your-key \
  -e LLM_PROVIDER=openrouter \
  -e LLM_MODEL=nvidia/nemotron-3-super-120b-a12b \
  -e GITHUB_TOKEN=ghp_xxx \
  code-review-ai review --diff /data/file.patch

# Or mount a .env file
docker run --env-file .env code-review-ai review --diff /data/file.patch
```

### Connection Test

On startup and after LLM config changes, the tool sends a minimal 1-token request to verify connectivity. This can be disabled:

```env
TEST_CONNECTION_ON_START=false
```

The connection test also runs automatically when you change provider, model, base URL, or API key via `config edit`, `config set`, or `Ctrl+P`.

---

## Quick Reference: Minimum `.env` by Provider

**OpenRouter (recommended for variety):**
```env
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-xxx
LLM_MODEL=openrouter/auto
```

**NVIDIA:**
```env
LLM_PROVIDER=nvidia
NVIDIA_API_KEY=nvapi-xxx
LLM_MODEL=nvidia/nemotron-3-super-120b-a12b
```

**Ollama (local):**
```env
# Use 'provider add' in interactive mode to register as a custom provider
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=llama3.1
RATE_LIMIT_RPM=0
REQUEST_TIMEOUT_SECONDS=300
```

**Any OpenAI-compatible server:**
```env
# Use 'provider add' in interactive mode to register as a custom provider
LLM_BASE_URL=http://your-server:8000/v1
LLM_API_KEY=your-key
LLM_MODEL=your-model-name
```
