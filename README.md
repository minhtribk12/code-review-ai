# Code Review Agent

Multi-agent code review CLI powered by NVIDIA Nemotron 3 Super.

Runs specialized agents (security, performance, style, test coverage) in parallel
to review GitHub pull requests or local diffs, then synthesizes findings into a
structured report.

## Quick Start

```bash
# Install dependencies
uv sync

# Configure API key
cp .env.example .env
# Edit .env with your OpenRouter or NVIDIA API key

# Review a local diff
code-review-agent review --diff path/to/file.patch

# Review a GitHub PR
code-review-agent review --pr owner/repo#123

# Save report to file
code-review-agent review --pr owner/repo#123 --output report.md
```

## Development

```bash
make install    # uv sync
make check      # lint + typecheck + test
make fmt        # auto-format code
make review     # run the tool
```
