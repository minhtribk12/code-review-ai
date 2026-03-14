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

## Architecture

```
CLI (Typer)
  -> Orchestrator
       -> [Security Agent]     \
       -> [Performance Agent]   |-- parallel execution (ThreadPoolExecutor)
       -> [Style Agent]         |
       -> [Test Coverage Agent]/
  -> Synthesizer (merges agent results)
  -> Reporter (outputs structured ReviewReport)
```

### Data Flow

```
CLI input (--diff or --pr)
    |
    v
ReviewInput ----------> Agent.review()
                              |
                              v
                        FindingsResponse  (from LLM)
                              |
                              v
                        AgentResult  (+ name, timing)
                              |
                    +---------+---------+
                    v                   v
              SynthesisResponse    agent_results
                    |                   |
                    +---------+---------+
                              v
                        ReviewReport --> Rich terminal / Markdown
```

### Iterative Review (planned)

Two feedback loops to improve accuracy:

```
                    DEEPENING LOOP                    VALIDATION LOOP
                (same agent, more context)         (different agent, checks work)
                --------------------------         ------------------------------

Round 0:  Agent sees diff --> [F1, F2, F3]
                                    |
Round 1:  Agent sees diff           |
          + "You previously   --> [F4, F5] (new)
            found F1,F2,F3.         |
            Look deeper."           |
                                    |
          (stop: no new findings    v
           or max_rounds hit)   All findings [F1..F5]
                                    |
                              Validator sees diff
                              + all findings ------> F2 rejected (false positive)
                                                     F3 severity: high -> medium
                                                     F1, F4, F5 confirmed
                                    |
                                    v
                              Final AgentResult (validated)
```

- **Deepening loop**: feeds previous findings back to the same agent so it can
  spot patterns it missed. Stops when no new findings or max rounds reached.
- **Validation loop**: a separate validator agent acts as a skeptical reviewer,
  filtering false positives and adjusting severity with reasoning.

## Development

```bash
make install    # uv sync
make check      # lint + typecheck + test
make fmt        # auto-format code
make review     # run the tool
```
