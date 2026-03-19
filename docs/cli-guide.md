# CLI Guide

The CLI provides one-shot commands for code review, findings navigation, and
launching the interactive REPL. Designed for scripting, CI/CD pipelines, and
quick terminal use.

## Key Highlights

- **Multi-agent analysis** -- security, performance, style, and test coverage
  run in parallel
- **Structured error reporting** -- every error shows what happened, why,
  and how to fix it
- **Rich terminal output** -- color-coded severity levels, progress
  indicators with live timers, and formatted tables
- **CI/CD ready** -- JSON output mode, quiet flag, and meaningful exit codes
- **Cost tracking** -- token usage and estimated cost in every report

## Installation and Setup

```bash
git clone https://github.com/minhtribk12/code-review-ai.git
cd code-review-ai
make install
cp .env.example .env
# Edit .env with your API key, or use the interactive setup on first launch
```

Two entry points are available:

```bash
uv run code-review-ai <command>   # full name
uv run cra <command>              # short alias
```

## Commands

### `review` -- Run a Code Review

The primary command. Accepts a local diff file or a GitHub PR reference,
dispatches agents in parallel, and outputs a structured report.

```bash
cra review --diff <path>       # review a local .patch file
cra review --pr owner/repo#123 # review a GitHub PR by shorthand
cra review --pr <full-url>     # review by full GitHub PR URL
```

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--diff` | path | -- | Path to a local unified diff / patch file |
| `--pr` | string | -- | GitHub PR reference (owner/repo#N or full URL) |
| `--agents` | string | tier default | Comma-separated agent names to run |
| `--format` | `rich\|json` | `rich` | Output format |
| `--output`, `-o` | path | -- | Save report to file (markdown or JSON) |
| `--quiet`, `-q` | flag | false | Suppress progress display |
| `--findings` | flag | false | Open interactive findings navigator after review |

`--diff` and `--pr` are mutually exclusive -- exactly one is required.

**Examples:**

```bash
# Review with only security and performance agents
cra review --diff changes.patch --agents security,performance

# Review and save as JSON (for CI)
cra review --pr acme/api#42 --format json --quiet > review.json

# Review and save as Markdown
cra review --diff changes.patch --output report.md

# Review and immediately navigate findings
cra review --diff changes.patch --findings

# Review with a custom YAML agent
cra review --diff changes.patch --agents security,django_security
```

**Exit codes:**

| Code | Meaning |
|------|---------|
| 0 | Review completed successfully |
| 1 | Error (bad input, missing config, review failure) |

**Progress display:**

During review, a live progress table shows each agent's status:

```
  security         >> running..   3.2s
  performance      OK done        2.1s
  style               waiting
  test_coverage    !! failed      1.5s

                   Press Ctrl+C for options
```

- `>>` **running** with animated dots and live elapsed timer
- `OK` **done** in green with final time
- `!!` **failed** in red with elapsed time
- `--` **cancelled** if aborted mid-review

**Ctrl+C** during a review gives three options:
1. **Abort** -- discard everything
2. **Finish** -- synthesize partial results from completed agents
3. **Continue** -- resume waiting

**Report output:**

The Rich report includes:
- **Header panel** with risk level (color-coded), finding counts
  per severity, token usage, and cost
- **Per-agent summaries** with finding counts and execution time
- **Findings table** sorted by severity with file locations

### `findings` -- Navigate Saved Review Findings

Opens the interactive findings navigator for a previously saved review from
the SQLite history database.

```bash
cra findings <review_id>
```

The `review_id` corresponds to the ID shown in `history` output. The navigator
opens in full-screen mode with the same key bindings as the REPL `findings`
command (see [Interactive Guide](interactive-guide.md#findings-navigator)).

### `interactive` -- Launch the REPL

Starts the interactive REPL with tab completion, command history, and a status
toolbar. This is where the full feature set lives -- git operations, PR
management, config editing, watch mode, and more.

```bash
cra interactive
```

See the [Interactive Guide](interactive-guide.md) for comprehensive REPL
documentation.

### Global Flags

| Flag | Description |
|------|-------------|
| `--version`, `-V` | Print version and exit |
| `--verbose`, `-v` | Enable DEBUG-level logging to stderr (overrides `LOG_LEVEL` setting) |

## Agent Selection

Agents are selected in this priority:

1. `--agents` flag (explicit override)
2. `DEFAULT_AGENTS` config setting (if non-empty)
3. Token tier defaults (`TOKEN_TIER` setting)

Tier defaults:

| Tier | Agents |
|------|--------|
| `free` | security |
| `standard` | security, performance, style, test_coverage |
| `premium` | security, performance, style, test_coverage |

Custom YAML agents can be used alongside built-in agents:

```bash
cra review --diff changes.patch --agents security,react_a11y,k8s_validator
```

See [Custom Agents](custom-agents.md) for how to define them.

## Token Budget

The diff is automatically truncated when it exceeds the token budget.
Files are sorted by change volume -- the most-changed files get full diffs,
the rest get one-line summaries.

Budget resolution (highest priority wins):

1. `MAX_PROMPT_TOKENS` -- explicit override
2. Model context window -- auto-detected from `LLM_MODEL`
3. `TOKEN_TIER` preset -- `free`=5k, `standard`=16k, `premium`=48k

## CI/CD Integration

The JSON output mode is designed for CI pipelines:

```bash
# Review and capture JSON output
cra review --pr $REPO#$PR_NUMBER --format json --quiet > review.json

# Parse findings with jq
jq '.agent_results[].findings[] | select(.severity == "critical")' review.json

# Fail CI if critical findings exist
CRITICAL=$(jq '[.agent_results[].findings[] | select(.severity == "critical")] | length' review.json)
if [ "$CRITICAL" -gt 0 ]; then
  echo "Found $CRITICAL critical findings"
  exit 1
fi
```

## Iterative Review

When `MAX_DEEPENING_ROUNDS` > 1, the review runs multiple passes. Each round
feeds previous findings back to agents, prompting them to look deeper.
The loop stops when a round produces zero new findings (convergence) or the
max is reached.

```env
MAX_DEEPENING_ROUNDS=2    # 2 rounds (1 initial + 1 deepening)
```

## Validation

When `IS_VALIDATION_ENABLED=true`, a separate validator agent reviews all
findings for false positives after synthesis. Findings marked
`likely_false_positive` are removed from the final report.

```env
IS_VALIDATION_ENABLED=true
MAX_VALIDATION_ROUNDS=1   # re-check uncertain findings
```

## Cost Estimation

The report includes estimated cost based on model pricing. Override
auto-detected pricing with custom rates:

```env
LLM_INPUT_PRICE_PER_M=0.30
LLM_OUTPUT_PRICE_PER_M=0.60
```

Cost scales with the number of agents, deepening rounds, and validation:

| Configuration | LLM Calls |
|---------------|-----------|
| 4 agents, 1 round, no validation | 4 + 1 synthesis = 5 |
| 4 agents, 2 rounds, validation | 8 + 1 synthesis + 1 validation = 10 |
| 2 agents, 1 round, no validation | 2 + 1 synthesis = 3 |

## Provider Management

The `provider` command manages LLM providers and models. In interactive mode, running `provider` (or `pv`) opens a full-screen browser.

### Sub-commands

| Command | Description |
|---------|-------------|
| `provider` | Open full-screen provider/model browser |
| `provider add` | Interactive wizard to add a custom provider |
| `provider list` | Table view of all providers |
| `provider models <name>` | List models for a specific provider |
| `provider remove <name>` | Remove a user-defined provider |

### Provider Browser Keys

| Key | Action |
|-----|--------|
| Enter | Expand/collapse provider |
| `a` | Add provider |
| `m` | Add model to selected provider |
| `d` | Delete (custom only) |
| `i` | Edit any field (including built-in) |
| `q` | Quit |

### Custom Providers

Add custom providers (e.g., Ollama, vLLM) via the browser or wizard:

```bash
cra> provider add
# or press 'a' in the provider browser
```

Custom providers are stored in `~/.cra/providers.json` and merged with bundled providers on startup.

## Error Handling

All errors are displayed with a structured format that helps you
quickly understand and resolve issues:

**Interactive mode (Rich panel):**

```
+--- Error ------------------------------------------------+
| GitHub API authentication failed                         |
|                                                          |
|   Reason: Your token is missing, expired, or lacks       |
|           the required permissions.                      |
|                                                          |
|   Fix:    Set GITHUB_TOKEN in your .env file or          |
|           environment. Ensure it has 'repo' scope for    |
|           private repos.                                 |
+----------------------------------------------------------+
```

**CLI mode (plain text):**

```
Error: GitHub API authentication failed
Reason: Your token is missing, expired, or lacks the required permissions.
Fix: Set GITHUB_TOKEN in your .env file or environment.
```

The error system covers:
- **GitHub API errors** -- auth, rate limits, not found, permissions
- **LLM provider errors** -- auth, model not found, timeout, rate limits
- **Configuration errors** -- missing keys, validation failures
- **Git errors** -- working tree issues, branch conflicts
- **File errors** -- not found, permissions

## Environment Variables

All configuration is done via environment variables or `.env` file.
See [Configuration](configuration.md) for the complete reference.

Essential variables:

```env
LLM_PROVIDER=nvidia                # nvidia or openrouter
NVIDIA_API_KEY=your-key-here       # required for nvidia provider
# OPENROUTER_API_KEY=your-key      # required for openrouter provider
GITHUB_TOKEN=ghp_your_token        # required for PR reviews
TOKEN_TIER=free                    # free, standard, premium
```
