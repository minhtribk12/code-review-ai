# Code Review Agent

Multi-agent code review CLI powered by LLMs. Runs specialized agents (security,
performance, style, test coverage) in parallel to review GitHub pull requests or
local diffs, then synthesizes findings into a structured report.

Built with Python, Typer, Pydantic, and the OpenAI-compatible API.

## Features

- **4 specialized agents** -- security, performance, style, test coverage
- **Parallel execution** -- all agents run concurrently via ThreadPoolExecutor
- **Structured output** -- findings with severity, category, file location, and suggestions
- **Multiple input modes** -- review GitHub PRs (`--pr`) or local diffs (`--diff`)
- **Multiple output formats** -- Rich terminal display and Markdown file export
- **Graceful degradation** -- partial results when individual agents fail
- **Retry with backoff** -- tenacity-powered retry for transient API errors
- **Provider-agnostic** -- works with OpenRouter, NVIDIA, OpenAI, or any OpenAI-compatible API

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager

### Install

```bash
git clone https://github.com/minhtribk12/code-review-agent.git
cd code-review-agent
make install
```

### Configure

```bash
cp .env.example .env
```

Edit `.env` and add your API key:

```env
LLM_API_KEY=your-api-key-here
LLM_PROVIDER=openrouter          # openrouter, nvidia, or openai
```

### Run

```bash
# Review a local diff
uv run code-review-agent review --diff path/to/file.patch

# Review a GitHub PR
uv run code-review-agent review --pr owner/repo#123

# Save markdown report
uv run code-review-agent review --diff file.patch --output report.md

# Debug mode
uv run code-review-agent --verbose review --diff file.patch
```

## Architecture

```
CLI (Typer)
  -> Orchestrator
       -> [Security Agent]     \
       -> [Performance Agent]   |-- parallel execution (ThreadPoolExecutor)
       -> [Style Agent]         |
       -> [Test Coverage Agent]/
  -> Synthesizer (merges agent results into summary + risk level)
  -> Reporter (Rich terminal / Markdown)
```

1. **Input** -- CLI parses a local `.patch` file or fetches a GitHub PR diff
2. **Dispatch** -- Orchestrator sends the diff to all 4 agents in parallel
3. **Review** -- Each agent analyzes the diff through its specialized lens
4. **Synthesis** -- A final LLM call merges all findings into an overall assessment
5. **Report** -- Findings are rendered as a Rich terminal report or saved as Markdown

See [docs/architecture.md](docs/architecture.md) for full design details and
decision rationale.

## Documentation

| Document | Description |
|----------|-------------|
| [docs/architecture.md](docs/architecture.md) | System design, pipeline flow, component responsibilities, design decisions |
| [docs/data-models.md](docs/data-models.md) | Pydantic models, StrEnums, LLM contracts |
| [docs/configuration.md](docs/configuration.md) | All settings, provider URL resolution, secrets handling |
| [interactive_tests/cli/README.md](interactive_tests/cli/README.md) | Mock LLM server and interactive test suite |

## Development

```bash
make install    # Install dependencies
make fmt        # Auto-format code
make lint       # Run ruff linter
make typecheck  # Run mypy (strict mode)
make test       # Run pytest with coverage
make check      # All of the above
```

### Test Suite

163 tests at 95% coverage:

| Component | Tests | Coverage |
|-----------|-------|----------|
| Models | 23 | 100% |
| Config | 8 | 100% |
| LLM Client | 31 | 93% |
| Agents | 42 | 100% |
| CLI | 33 | 92% |
| Report | 18 | 100% |
| Orchestrator | 5 | 70% |
| GitHub Client | 11 | 94% |

### Interactive Tests

Run the CLI against a mock LLM server (no API key needed):

```bash
bash interactive_tests/cli/run_all_tests.sh
```

This starts a local FastAPI mock server and runs 16 scenarios covering help,
version, diff parsing, error handling, and report generation.

## Project Structure

```
src/code_review_agent/
  agents/
    base.py              # BaseAgent ABC with validation + error handling
    security.py          # OWASP-focused security review
    performance.py       # Complexity, memory, I/O analysis
    style.py             # Naming, readability, dead code
    test_coverage.py     # Missing tests, edge cases
  config.py              # Settings with pydantic-settings
  llm_client.py          # OpenAI-compatible client with retry + JSON parsing
  models.py              # Pydantic models + StrEnums
  orchestrator.py        # Parallel agent execution + synthesis
  main.py                # Typer CLI
  report.py              # Rich terminal + Markdown rendering
  github_client.py       # PR diff fetching

tests/                   # 163 unit tests
interactive_tests/       # E2E tests with mock LLM server
```

## License

Apache License 2.0 -- see [LICENSE](LICENSE) for details.
