# Code Review Agent -- Implementation Plan

A complete guide to building this project from scratch. Every design decision,
every file, and every tool choice is explained with the reasoning behind it.

---

## Table of Contents

1. [Phase 0: Project Foundation](#phase-0-project-foundation)
2. [Phase 1: Single Agent MVP](#phase-1-single-agent-mvp-weekend-1)
3. [Phase 2: Multi-Agent Orchestration](#phase-2-multi-agent-with-parallel-execution-weekend-2)
4. [Phase 3: GitHub Integration](#phase-3-github-integration-weekend-3)
5. [Phase 4: Polish and Portfolio](#phase-4-polish-and-portfolio-weekend-4)
6. [Phase 5: Advanced Features](#phase-5-advanced-features-optional)
7. [Appendix A: Tooling Deep Dive](#appendix-a-tooling-deep-dive)
8. [Appendix B: Every File Explained](#appendix-b-every-file-explained)
9. [Appendix C: Design Decisions](#appendix-c-design-decisions)

---

## Phase 0: Project Foundation

**Goal:** Set up a professional Python project from scratch with all tooling configured.
This is the infrastructure you build ONCE and it pays off for the entire project lifetime.

### Step 0.1: Initialize the project (30 min)

```bash
mkdir code-review-agent && cd code-review-agent
git init && git checkout -b main
```

**Decision: Why a standalone git repo?**
This project will live on your GitHub profile as a portfolio piece. It needs its own
repo, not buried inside another project. Recruiters and hiring managers will look at
this repo directly.

### Step 0.2: Create pyproject.toml (30 min)

This is the single source of truth for your entire project. Before `pyproject.toml`
(PEP 621), Python projects needed `setup.py`, `setup.cfg`, `requirements.txt`,
`MANIFEST.in`, and separate config files for each tool. Now everything goes in one file.

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

**Why hatchling?** It's the build backend (the thing that creates installable packages).
Alternatives are setuptools (old, verbose) and flit (simpler but less flexible).
Hatchling is fast, modern, and works perfectly with uv.

```toml
[project]
name = "code-review-agent"
version = "0.1.0"
description = "Multi-agent code review CLI using NVIDIA Nemotron 3 Super"
readme = "README.md"
requires-python = ">=3.12"
license = { text = "MIT" }
```

**Why Python 3.12+?** It has the best type hint syntax (`str | None` instead of
`Optional[str]`), better error messages, and performance improvements. No reason
to support older versions for a new project.

```toml
dependencies = [
    "typer>=0.12",          # CLI framework (builds on Click, adds type hints)
    "rich>=13.0",           # Beautiful terminal output (tables, panels, colors)
    "pydantic>=2.0",        # Data validation and serialization
    "pydantic-settings",    # Load config from .env files and environment
    "httpx",                # Modern HTTP client (like requests but with async support)
    "openai",               # OpenAI-compatible SDK (works with any provider)
    "pygithub",             # GitHub API client (not used directly, but available)
    "structlog",            # Structured logging (JSON-friendly, context-aware)
]
```

**Why each dependency:**

| Dependency | Why not the alternative |
|---|---|
| typer | argparse is verbose and has no type hints. Click is lower-level. Typer gives you a CLI from type-annotated functions. |
| rich | print() is ugly. Rich gives you tables, panels, progress bars, syntax highlighting with zero effort. |
| pydantic | dataclasses don't validate. attrs is less popular. Pydantic v2 is the standard for data validation in Python. |
| httpx | requests doesn't support async. urllib3 is too low-level. httpx is the modern choice. |
| openai | The OpenAI SDK works with ANY provider that implements the OpenAI-compatible API (OpenRouter, NVIDIA, Together, etc.). One client, many providers. |
| structlog | stdlib logging is stringly-typed. structlog gives you key=value pairs that are machine-parseable and human-readable. |

```toml
[project.scripts]
code-review-agent = "code_review_agent.main:app"
```

This line means: when someone runs `code-review-agent` in their terminal, Python calls
the `app` object in `src/code_review_agent/main.py`. This is how CLI tools are distributed.

```toml
[dependency-groups]
dev = ["ruff", "mypy", "pre-commit", "pytest", "pytest-cov", "pytest-asyncio", "respx", "detect-secrets"]
```

**Why dependency-groups instead of `[project.optional-dependencies]`?**
Dependency groups (PEP 735) are for development tools that are NEVER shipped to users.
Optional dependencies are for features users can opt into (like `pip install mylib[postgres]`).
Dev tools should never be in the production package.

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/code_review_agent"]
```

**Why src/ layout?** Without it, `import code_review_agent` might accidentally import
from your local source directory instead of the installed package. The src/ layout forces
Python to use the installed version, catching packaging bugs early. Every serious Python
project uses src/ layout.

#### Ruff configuration explained

```toml
[tool.ruff]
line-length = 99      # 79 (PEP 8) is too narrow for modern screens. 99 is practical.
target-version = "py312"  # Enables Python 3.12-specific lint rules and fixes.

[tool.ruff.lint]
select = [
    "E",    # pycodestyle errors (basic syntax style)
    "F",    # pyflakes (unused imports, undefined names, etc.)
    "W",    # pycodestyle warnings
    "I",    # isort (import ordering -- stdlib, third-party, local)
    "UP",   # pyupgrade (use modern Python syntax, e.g., dict | instead of Dict)
    "B",    # flake8-bugbear (common bugs, e.g., mutable default arguments)
    "SIM",  # flake8-simplify (simplifiable code patterns)
    "TCH",  # flake8-type-checking (move type-only imports to TYPE_CHECKING blocks)
    "RUF",  # ruff-specific rules (catch ruff-specific issues)
    "ANN",  # flake8-annotations (enforce type hints on all functions)
    "S",    # flake8-bandit (security checks -- hardcoded passwords, SQL injection, etc.)
    "PT",   # flake8-pytest-style (pytest best practices)
]
ignore = ["S101"]  # Allow assert in tests (bandit flags assert as insecure)
```

**Why do we need a linter at all?**

Without a linter, these bugs slip into production:
- Unused imports that slow down startup and confuse readers
- Variables that shadow builtins (naming a variable `list` or `id`)
- Security issues (hardcoded passwords, SQL injection patterns)
- Inconsistent import ordering (causes messy git diffs)
- Missing type hints (defeats the purpose of using mypy)

Ruff replaces 7 separate tools (black, isort, flake8, pyflakes, pycodestyle, bandit,
flake8-bugbear) with one tool that runs in milliseconds. It's written in Rust.

```toml
[tool.ruff.lint.per-file-ignores]
"tests/**/*.py" = ["S106"]   # Allow fake tokens in tests (bandit flags them)
"src/code_review_agent/main.py" = ["B008"]   # Typer requires function calls in defaults
"src/code_review_agent/models.py" = ["TC003"] # Pydantic needs datetime at runtime
```

**Why per-file-ignores instead of inline `# noqa` comments?**
It documents the policy in one place. When a new team member asks "why are we
allowing hardcoded passwords?", they find the answer in pyproject.toml, not scattered
across 20 test files.

#### Mypy configuration

```toml
[tool.mypy]
strict = true
```

**Why strict mode?** It enables ALL mypy checks: no implicit `Any`, no untyped
functions, no missing return types. This catches entire categories of bugs at
development time that would otherwise be runtime errors. Strict is hard at first
but saves you hours of debugging.

**What mypy catches that ruff doesn't:**
- Passing a string where an int is expected
- Calling a method that doesn't exist on a type
- Returning the wrong type from a function
- Missing None checks (the billion-dollar mistake)

#### Pytest configuration

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "--cov=code_review_agent --cov-report=term-missing"
```

`--cov` measures code coverage automatically on every test run. `--cov-report=term-missing`
shows which lines are NOT covered, so you know exactly where to add tests.

### Step 0.3: Create .pre-commit-config.yaml (15 min)

Pre-commit hooks run checks automatically before every `git commit`. If a check
fails, the commit is rejected. This prevents bad code from ever entering the repo.

```yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    hooks:
      - id: trailing-whitespace    # Remove trailing spaces (causes noisy git diffs)
      - id: end-of-file-fixer      # Ensure files end with a newline (POSIX standard)

  - repo: https://github.com/astral-sh/ruff-pre-commit
    hooks:
      - id: ruff                    # Lint check (catches bugs before commit)
        args: [--fix, --exit-non-zero-on-fix]  # Auto-fix what it can
      - id: ruff-format            # Format code (consistent style, no debates)

  - repo: https://github.com/pre-commit/mirrors-mypy
    hooks:
      - id: mypy                   # Type check (catches type errors before commit)

  - repo: https://github.com/Yelp/detect-secrets
    hooks:
      - id: detect-secrets         # Catches accidentally committed API keys/tokens
```

**Why pre-commit hooks?**
Without hooks, you rely on discipline: "I'll remember to run the linter." You won't.
Hooks make quality automatic. They also catch secrets -- one committed API key can
cost thousands of dollars if scraped by bots.

### Step 0.4: Create .github/workflows/ci.yml (15 min)

CI (Continuous Integration) runs the same checks on GitHub's servers every time
you push code or open a PR. Even if you skip pre-commit locally, CI catches it.

```yaml
name: CI
on:
  push:
    branches: [main]        # Run on every push to main
  pull_request:
    branches: [main]        # Run on every PR targeting main

permissions:
  contents: read            # Least privilege -- CI only needs to read code

jobs:
  check:
    runs-on: ubuntu-latest  # Free GitHub-hosted runner
    steps:
      - uses: actions/checkout@v4       # Clone the repo
      - uses: actions/setup-python@v5   # Install Python 3.12
        with:
          python-version: "3.12"
      - uses: astral-sh/setup-uv@v5    # Install uv
      - run: uv sync                    # Install all dependencies
      - run: make lint                   # Ruff lint check
      - run: make typecheck              # Mypy strict check
      - run: make test                   # Pytest with coverage
```

**Why CI matters for a portfolio project:**
- Green CI badge on your README signals professionalism
- Hiring managers check if a project has tests and CI
- It proves your code actually works on a clean machine (not just "works on my laptop")

### Step 0.5: Create Makefile (10 min)

```makefile
install:   uv sync               # Install everything
fmt:       uv run ruff format .  # Auto-format code
lint:      uv run ruff check .   # Check for issues
typecheck: uv run mypy src/      # Type checking
test:      uv run pytest         # Run tests
check:     lint typecheck test   # Run all checks (one command)
```

**Why Makefile in 2026?**
It's a universal interface. Every developer knows `make check`. Whether you use uv, pip,
poetry, or conda underneath, the Makefile abstracts it away. New contributors don't need
to know your tooling -- they just run `make check`.

### Step 0.6: Create Dockerfile (15 min)

```dockerfile
# Stage 1: Builder -- install dependencies
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen --no-install-project  # Install deps without source
COPY src/ src/
RUN uv sync --no-dev --frozen                        # Install the project itself

# Stage 2: Runtime -- minimal image
FROM python:3.12-slim AS runtime
RUN groupadd --system app && useradd --system --gid app app  # Non-root user
COPY --from=builder /app/.venv /app/.venv  # Copy only the virtualenv
ENV PATH="/app/.venv/bin:${PATH}"
USER app                    # Never run as root in containers
ENTRYPOINT ["code-review-agent"]
```

**Why multi-stage builds?**
Stage 1 (builder) has build tools, compilers, uv -- everything needed to install.
Stage 2 (runtime) has ONLY the virtualenv with installed packages. Result: smaller
image, fewer security vulnerabilities, faster deploys.

**Why non-root user?**
If an attacker exploits a vulnerability in your app, they get the permissions of the
running user. Root = full control of the container (and possibly the host). Non-root
limits the blast radius.

### Step 0.7: Create .gitignore, .env.example, .python-version (5 min)

- `.gitignore`: Excludes `__pycache__`, `.env`, `dist/`, IDE files. Includes `uv.lock`
  (lock files must be committed for deterministic builds).
- `.env.example`: Documents required environment variables. Never commit `.env` itself.
- `.python-version`: Tells uv and pyenv which Python version to use. Single source
  of truth for the Python version.

### Phase 0 checkpoint
- [ ] `git init` done, `.gitignore` in place
- [ ] `pyproject.toml` with all deps and tool configs
- [ ] `.pre-commit-config.yaml` ready
- [ ] `.github/workflows/ci.yml` ready
- [ ] `Makefile` with standard targets
- [ ] `Dockerfile` with multi-stage build
- [ ] `.env.example` with all required variables
- [ ] `uv sync` runs without errors

---

## Phase 1: Single Agent MVP (Weekend 1)

**Goal:** Get ONE agent (security) reviewing a local diff file end-to-end.

### Step 1.1: Design the data model (1 hour)

Before writing any logic, define the data structures. This is the most important
design step because every other component depends on these models.

**Design decision: Why Pydantic frozen models?**

```python
class Finding(BaseModel):
    model_config = {"frozen": True}  # Immutable -- cannot be modified after creation

    severity: Literal["critical", "high", "medium", "low"]  # Constrained values
    category: str
    title: str
    description: str
    file_path: str | None = None     # Optional -- not all findings have a location
    line_number: int | None = None
    suggestion: str | None = None
```

- **Frozen**: Once created, a Finding cannot be modified. This prevents bugs where
  one component accidentally mutates data that another component is reading.
- **Literal types**: `severity` can only be one of 4 values. If you typo "hihg",
  Pydantic raises a validation error immediately instead of silently passing bad data.
- **Optional fields with None defaults**: Not every finding can point to a specific
  line. Making these optional keeps the model flexible.

**The full model hierarchy:**

```
Finding         -- one issue found by an agent
AgentResult     -- all findings from one agent + summary + timing
ReviewReport    -- all agent results + overall summary + risk level
DiffFile        -- one file's diff content
ReviewInput     -- all diffs + PR metadata (input to the pipeline)
FindingsResponse -- what the LLM returns (list of Finding + summary)
SynthesisResponse -- what the synthesis LLM returns (summary + risk)
```

**Why separate FindingsResponse and SynthesisResponse?**
These are the exact JSON shapes we tell the LLM to produce. By making them Pydantic
models, we get automatic validation of LLM output. If the LLM returns garbage, Pydantic
throws a clear error instead of silently propagating bad data.

### Step 1.2: Build the configuration layer (30 min)

```python
class Settings(BaseSettings):
    llm_provider: str = "openrouter"
    llm_api_key: SecretStr           # Never printed in logs or error messages
    llm_model: str = "nvidia/nemotron-3-super-120b-a12b"
    github_token: SecretStr | None = None
    max_concurrent_agents: int = 4

    @computed_field
    @property
    def llm_base_url(self) -> str:
        """Map provider name to API base URL."""
        urls = {"openrouter": "https://openrouter.ai/api/v1", ...}
        return urls.get(self.llm_provider.lower(), self.llm_provider)

    model_config = {"env_file": ".env"}
```

**Key design decisions:**

- **SecretStr**: `print(settings.llm_api_key)` shows `**********`, not the actual key.
  This prevents accidental logging of credentials.
- **computed_field for base_url**: The provider name ("openrouter") is what users
  configure, but the actual URL is an implementation detail. Computing it means users
  don't need to know URLs.
- **env_file**: Settings load from `.env` file automatically. No manual env parsing.
  Priority: CLI flags > env vars > .env file > defaults.

### Step 1.3: Build the LLM client (1 hour)

```python
class LLMClient:
    def __init__(self, settings: Settings) -> None:
        self._client = openai.OpenAI(
            api_key=settings.llm_api_key.get_secret_value(),
            base_url=settings.llm_base_url,
        )

    def complete(self, *, system_prompt: str, user_prompt: str,
                 response_model: type[T]) -> T:
        # 1. Inject JSON schema into the system prompt
        # 2. Call the LLM
        # 3. Strip markdown fences from response
        # 4. Validate through Pydantic
        return response_model.model_validate_json(cleaned_response)
```

**Design decision: Why inject JSON schema into the prompt?**

The OpenAI SDK has a `response_format` parameter, but not all providers support it
(OpenRouter sometimes, NVIDIA API sometimes). By injecting the Pydantic JSON schema
into the system prompt, we get structured output from ANY provider. The LLM sees:

```
You MUST respond with valid JSON matching this schema:
{"properties": {"findings": [...], "summary": {"type": "string"}}, ...}
```

This is more reliable across providers than relying on API-specific features.

**Why strip markdown fences?**
LLMs often wrap JSON in ` ```json ... ``` ` even when told not to. The client handles
this automatically so agents don't need to worry about it.

### Step 1.4: Build the base agent (30 min)

```python
class BaseAgent(ABC):
    name: str               # "security", "performance", etc.
    system_prompt: str      # The specialized prompt for this agent

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm_client = llm_client

    def review(self, review_input: ReviewInput) -> AgentResult:
        user_prompt = self._format_user_prompt(review_input)
        start = time.monotonic()
        response = self._llm_client.complete(
            system_prompt=self.system_prompt,
            user_prompt=user_prompt,
            response_model=FindingsResponse,
        )
        elapsed = time.monotonic() - start
        return AgentResult(agent_name=self.name, findings=response.findings, ...)
```

**Design decision: Why ABC (Abstract Base Class)?**

All 4 agents do the same thing: format a prompt, call the LLM, parse the response.
The only difference is the system prompt. ABC enforces that every agent sets `name`
and `system_prompt`, while inheriting the shared `review()` logic. This is the
**Template Method pattern** -- define the skeleton, let subclasses fill in the details.

**Why time.monotonic() instead of time.time()?**
`time.time()` can jump backwards if the system clock is adjusted (NTP sync, daylight
saving). `time.monotonic()` always moves forward. For measuring elapsed time, always
use monotonic.

### Step 1.5: Build the security agent (30 min)

```python
class SecurityAgent(BaseAgent):
    name = "security"
    system_prompt = (
        "You are an expert security code reviewer. Analyze the provided code diff "
        "for security vulnerabilities and risks.\n\n"
        "Focus areas:\n"
        "- OWASP Top 10 vulnerabilities ...\n"
        "- Hardcoded secrets ...\n"
        ...
    )
```

The entire agent is just a class with a name and a prompt. All logic is inherited
from `BaseAgent`. Adding a new agent type is just writing a new prompt.

### Step 1.6: Build the CLI (1 hour)

```python
app = typer.Typer(name="code-review-agent")

@app.command()
def review(
    pr: str | None = typer.Option(None, "--pr"),
    diff: Path | None = typer.Option(None, "--diff"),
    output: Path | None = typer.Option(None, "--output", "-o"),
) -> None:
    settings = Settings()
    review_input = _build_review_input(pr=pr, diff=diff, settings=settings)
    llm_client = LLMClient(settings=settings)
    orchestrator = Orchestrator(settings=settings, llm_client=llm_client)
    report = orchestrator.run(review_input=review_input)
    render_report_rich(report=report)
```

**Why Typer over argparse/Click?**

Typer generates the CLI from your function's type annotations:
- `pr: str | None` automatically becomes `--pr` with an optional string argument
- `Path` type automatically validates the file exists
- Help text comes from the `help=` parameter

With argparse, this would be 30+ lines of boilerplate.

### Step 1.7: Build the report renderer (30 min)

Two output formats:
- **Rich terminal**: Panels, colored tables, severity badges -- for interactive use
- **Markdown**: For saving to files, pasting into PRs, sharing

### Step 1.8: Write tests (1-2 hours)

See [Appendix B](#appendix-b-every-file-explained) for test details. Key principles:
- Mock the LLM client -- never make real API calls in tests
- Test Pydantic models (creation, immutability, serialization)
- Test report rendering (all sections present, handles empty data)
- Use `conftest.py` for shared fixtures (sample data reused across test files)

### Step 1.9: Test manually and iterate (1 hour)

```bash
cp .env.example .env   # Add your API key
mkdir -p scratch
# Create a sample diff, then:
uv run code-review-agent review --diff scratch/sample.patch
```

### Phase 1 checkpoint
- [ ] `make check` passes
- [ ] Can review a local diff with security agent
- [ ] Report renders in terminal and saves to markdown

---

## Phase 2: Multi-Agent with Parallel Execution (Weekend 2)

**Goal:** All 4 agents run in parallel, results are synthesized.

### Step 2.1: Add remaining agents (1 hour)

Copy the SecurityAgent pattern for:
- `PerformanceAgent`: N+1 queries, complexity, memory leaks, blocking I/O
- `StyleAgent`: naming, dead code, readability, type hints
- `TestCoverageAgent`: missing tests, edge cases, test quality

Each agent is just a class with a different `system_prompt`.

### Step 2.2: Build the orchestrator (2 hours)

```python
class Orchestrator:
    def run(self, review_input: ReviewInput) -> ReviewReport:
        agents = [SecurityAgent(...), PerformanceAgent(...), StyleAgent(...), TestCoverageAgent(...)]
        agent_results = self._run_agents(agents, review_input)  # Parallel
        synthesis = self._synthesize(agent_results)              # One more LLM call
        return ReviewReport(agent_results=agent_results, overall_summary=synthesis.overall_summary, ...)

    def _run_agents(self, agents, review_input) -> list[AgentResult]:
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_agent = {executor.submit(agent.review, review_input): agent for agent in agents}
            for future in as_completed(future_to_agent):
                try:
                    results.append(future.result())
                except Exception:
                    logger.exception("agent failed, continuing")  # Graceful degradation
        return results
```

**Design decision: Why ThreadPoolExecutor, not asyncio?**

- LLM API calls are **I/O-bound** (waiting for network response), not CPU-bound
- Threads release the GIL during I/O, so 4 threads = 4 parallel API calls
- Threading is simpler than async (no `async/await` everywhere, no event loop management)
- The OpenAI SDK's synchronous client works natively with threads
- asyncio would be better at 100+ concurrent calls, but we have exactly 4

**Design decision: Why graceful degradation?**

If the performance agent times out, you still want the security, style, and test
results. A partial review is better than no review. The `try/except` around each
`future.result()` ensures one failure doesn't crash the entire pipeline.

**Design decision: Why a synthesis step?**

4 agents produce 4 independent analyses. A user wants ONE summary: "Is this PR safe
to merge?" The synthesis step sends all findings to the LLM and asks for an overall
assessment and risk level. This is the "senior engineer" that reads all the reviews
and makes the final call.

### Step 2.3: Test and iterate (2 hours)

- Run with `--verbose` to see parallel execution in the logs
- Verify timing: 4 agents in parallel should take ~1x single agent time, not 4x
- Cost: 5 LLM calls per review (4 agents + 1 synthesis)

### Phase 2 checkpoint
- [ ] All 4 agents run in parallel
- [ ] Synthesis produces meaningful summary and risk level
- [ ] Graceful degradation works (1 agent fails, report still generated)
- [ ] `make check` passes

---

## Phase 3: GitHub Integration (Weekend 3)

**Goal:** Review real GitHub PRs directly from the CLI.

### Step 3.1: Build the GitHub client (2 hours)

```python
def parse_pr_reference(pr_ref: str) -> tuple[str, str, int]:
    """Parse 'owner/repo#123' or full URL into (owner, repo, pr_number)."""

def fetch_pr_diff(*, owner, repo, pr_number, token) -> ReviewInput:
    """Fetch PR diff and metadata from GitHub REST API."""
```

**Design decision: Why httpx instead of PyGithub?**

We only need 2 API calls (PR metadata + PR files). PyGithub wraps the entire GitHub API
with dozens of classes. httpx gives us exactly what we need with zero abstraction overhead.
PyGithub is listed as a dependency for future use (commenting on PRs, etc.).

**Design decision: Why functions, not a class?**

These are stateless operations. A class would just wrap the functions with no added value.
Functions are simpler, easier to test, and easier to mock.

### Step 3.2: Handle edge cases (1-2 hours)

- Large PRs (>50 files): filter to only code files, or chunk into batches
- Binary files: skip files without `patch` field in the GitHub API response
- Rate limiting: GitHub allows 5000 req/hr with a token, 60 without
- Invalid input: clear error messages for bad PR references

### Step 3.3: Test with real PRs (1 hour)

```bash
uv run code-review-agent review --pr YOUR_USER/YOUR_REPO#PR_NUMBER
```

### Phase 3 checkpoint
- [ ] Can review any public GitHub PR
- [ ] Handles private repos with token auth
- [ ] Edge cases handled gracefully
- [ ] `make check` passes

---

## Phase 4: Polish and Portfolio (Weekend 4)

**Goal:** Make this portfolio-ready.

### Step 4.1: Professional README (1 hour)

- Architecture diagram (mermaid or ASCII)
- Quick start guide
- Example output (terminal screenshot)
- Supported providers table
- "How to add a new agent" section

### Step 4.2: Record a demo (1 hour)

- Use [asciinema](https://asciinema.org) for terminal recording
- 2-minute demo: run review, show output, show saved report

### Step 4.3: Publish to GitHub (30 min)

```bash
git remote add origin git@github.com:YOUR_USER/code-review-agent.git
git push -u origin main
```

Add topics: `llm`, `code-review`, `multi-agent`, `nemotron`, `ai`, `python`

### Step 4.4: Write a blog post (2-3 hours)

Topics: multi-agent architecture, structured output, prompt engineering lessons.

### Phase 4 checkpoint
- [ ] README with architecture diagram and examples
- [ ] CI badge green
- [ ] Demo recording
- [ ] Published on GitHub
- [ ] Blog post drafted

---

## Phase 5: Advanced Features (Optional)

| Feature | What | Skill signal |
|---|---|---|
| GitHub Action | Auto-review PRs on push | CI/CD, developer tooling |
| Async execution | Replace threads with asyncio | Modern concurrency |
| Agent memory | Vector store for project patterns | RAG, embeddings |
| Custom agents | YAML-defined agents with custom prompts | Plugin architecture |
| MCP server | Expose as Model Context Protocol tool | Emerging standards |
| Eval framework | Precision/recall on known-vulnerable code | ML engineering rigor |

---

## Appendix A: Tooling Deep Dive

### Why we need each tool and what it catches

#### uv (package manager)

**What it replaces:** pip, pip-tools, virtualenv, poetry, pipenv
**Why:** uv is 10-100x faster than pip. Written in Rust. Creates deterministic lock
files (`uv.lock`). Handles virtual environments automatically. One tool instead of five.

```bash
uv sync          # Install everything from uv.lock (deterministic)
uv add httpx     # Add a dependency (updates pyproject.toml and uv.lock)
uv run pytest    # Run a command inside the virtual environment
```

#### ruff (linter + formatter)

**What it replaces:** black, isort, flake8, pylint, bandit, pycodestyle, pyflakes, flake8-bugbear
**Why:** One tool, millisecond performance, 800+ rules. Written in Rust.

**What each rule category catches:**

| Code | Name | What it catches | Example |
|---|---|---|---|
| `E` | pycodestyle | Basic style (spacing, indentation) | `x=1` instead of `x = 1` |
| `F` | pyflakes | Unused imports, undefined names | `import os` when os is never used |
| `W` | warnings | Style warnings | Trailing whitespace |
| `I` | isort | Wrong import order | Local imports before stdlib |
| `UP` | pyupgrade | Old Python syntax | `Dict[str, int]` instead of `dict[str, int]` |
| `B` | bugbear | Common bugs | Mutable default arguments `def f(x=[])` |
| `SIM` | simplify | Unnecessarily complex code | `if x == True` instead of `if x` |
| `TCH` | type-checking | Import optimization | Runtime imports used only for type hints |
| `RUF` | ruff-specific | Ruff's own rules | Unsorted `__all__` |
| `ANN` | annotations | Missing type hints | `def f(x):` without type annotations |
| `S` | bandit/security | Security issues | Hardcoded passwords, SQL injection patterns |
| `PT` | pytest-style | Test best practices | `pytest.raises(ValueError)` without `match=` |

#### mypy (type checker)

**What it does:** Reads your type annotations and checks that you're using types correctly.
Think of it as a compiler for Python.

```python
def add(a: int, b: int) -> int:
    return a + b

add("hello", "world")  # mypy error: expected int, got str
```

**Why strict mode?**
Normal mode lets untyped code slide. Strict mode requires type hints everywhere and
catches more bugs. Yes, it's more work upfront, but it catches bugs that would otherwise
be production incidents.

#### pytest (test framework)

**What it does:** Discovers and runs test functions, provides assertions, fixtures, and mocking.

**Key concepts used in this project:**
- **Fixtures** (`conftest.py`): Reusable test data created once, injected into tests
- **Parametrize**: Run the same test with different inputs
- **tmp_path**: Pytest provides a temporary directory, cleaned up automatically
- **Coverage** (`--cov`): Measures which lines of your code are executed during tests

#### pre-commit (git hooks)

**What it does:** Runs checks before every `git commit`. If any check fails, the commit
is blocked.

**Our hooks in order:**
1. `trailing-whitespace` -- removes trailing spaces (noisy diffs)
2. `end-of-file-fixer` -- ensures files end with newline (POSIX standard)
3. `ruff` -- lint + auto-fix
4. `ruff-format` -- format code
5. `mypy` -- type check
6. `detect-secrets` -- catches committed API keys

#### GitHub Actions CI

**What it does:** Runs the same checks on GitHub's servers on every push/PR.

**Why both pre-commit AND CI?**
- Pre-commit: catches issues locally before pushing (fast feedback)
- CI: catches issues even if someone skips pre-commit (safety net)
- CI runs on a clean machine, catching "works on my machine" bugs

---

## Appendix B: Every File Explained

### Source files

| File | Lines | Purpose |
|---|---|---|
| `src/code_review_agent/__init__.py` | 3 | Package marker. Exports nothing (agents are accessed via submodules). |
| `src/code_review_agent/models.py` | 100 | All data structures. 7 frozen Pydantic models. This is the schema for the entire project. |
| `src/code_review_agent/config.py` | 38 | Settings from environment. Maps provider names to API URLs. SecretStr for credentials. |
| `src/code_review_agent/llm_client.py` | 86 | Thin OpenAI SDK wrapper. Injects JSON schema, strips markdown fences, validates response. |
| `src/code_review_agent/agents/base.py` | 72 | ABC defining the agent interface. Template method: format prompt, call LLM, time it, return result. |
| `src/code_review_agent/agents/security.py` | 37 | Security agent. OWASP-focused system prompt. Inherits all logic from BaseAgent. |
| `src/code_review_agent/agents/performance.py` | ~37 | Performance agent. Complexity, N+1 queries, memory leaks. |
| `src/code_review_agent/agents/style.py` | ~37 | Style agent. Naming, dead code, readability. |
| `src/code_review_agent/agents/test_coverage.py` | ~37 | Test coverage agent. Missing tests, edge cases. |
| `src/code_review_agent/orchestrator.py` | 125 | Runs agents in parallel with ThreadPoolExecutor. Graceful degradation. Synthesis step. |
| `src/code_review_agent/github_client.py` | 102 | Parses PR references. Fetches diffs from GitHub API via httpx. |
| `src/code_review_agent/main.py` | 180 | Typer CLI. `review` command with `--pr`, `--diff`, `--output` options. Unified diff parser. |
| `src/code_review_agent/report.py` | 153 | Rich terminal output and markdown rendering. Color-coded severity. Save to file. |

### Test files

| File | Tests | What it validates |
|---|---|---|
| `tests/conftest.py` | 0 (fixtures) | 8 shared fixtures: sample data, mock settings, mock LLM client |
| `tests/test_models.py` | 23 | Pydantic model creation, immutability, severity validation, serialization roundtrip |
| `tests/test_github_client.py` | 11 | PR reference parsing (valid + invalid), API response handling, auth headers |
| `tests/test_orchestrator.py` | 5 | Agent dispatch, graceful degradation, report assembly, finding aggregation |
| `tests/test_report.py` | 8 | Markdown sections present, finding details, empty data handling, file save |

### Config files

| File | Purpose |
|---|---|
| `pyproject.toml` | Project metadata, dependencies, tool configs (ruff, mypy, pytest) |
| `.python-version` | Pins Python 3.12 for uv and pyenv |
| `.pre-commit-config.yaml` | Git hooks: ruff, mypy, detect-secrets |
| `.github/workflows/ci.yml` | GitHub Actions CI pipeline |
| `Makefile` | Standard command interface (make check, make fmt, etc.) |
| `Dockerfile` | Multi-stage build, non-root user, uv-based install |
| `.gitignore` | Excludes caches, .env, IDE files. Includes uv.lock. |
| `.env.example` | Documents required env vars (API keys, provider, model) |
| `CLAUDE.md` | AI assistant instructions specific to this project |
| `README.md` | Project documentation for humans |

---

## Appendix C: Design Decisions

### Architecture: Why multi-agent instead of one big prompt?

| Approach | Pros | Cons |
|---|---|---|
| Single prompt | Simpler, cheaper (1 API call) | Too many concerns in one prompt degrades quality. Hard to tune. |
| Multi-agent | Specialized prompts = better results. Parallelizable. Independently tunable. | More API calls (5 total). More complex orchestration. |

We chose multi-agent because:
1. Each agent is a specialist -- security expertise doesn't help with style review
2. Agents run in parallel -- total time = ~1 agent, not 4
3. You can add/remove agents without affecting others
4. You can tune each agent's prompt independently
5. Nemotron 3 Super was literally designed for multi-agent workloads

### Why OpenAI-compatible API instead of raw HTTP?

The OpenAI SDK is the de facto standard. OpenRouter, NVIDIA, Together, Fireworks,
and dozens of other providers all implement the same API. By using the OpenAI SDK
with a configurable `base_url`, we support all of them with zero code changes.

### Why Literal types instead of Enum for severity?

```python
# We use this:
severity: Literal["critical", "high", "medium", "low"]

# Instead of this:
class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    ...
```

The LLM returns JSON strings. Literal types validate directly against strings.
Enums require extra serialization logic and make the JSON schema more complex
for the LLM to follow. Simpler = more reliable LLM output.

### Why not use LangChain or CrewAI?

These frameworks add complexity we don't need:
- LangChain: massive dependency tree, abstractions over abstractions, hard to debug
- CrewAI: opinionated agent framework, adds its own patterns on top of ours

Our orchestrator is 125 lines of Python. It does exactly what we need with zero
framework overhead. When you can explain every line of your code in an interview,
that's more impressive than "I used LangChain."

For a portfolio project, showing you understand the fundamentals (threading,
API calls, prompt engineering) is more valuable than showing you can configure
a framework.

---

## Recommended Reading List

| Topic | Resource | Time |
|---|---|---|
| Agentic AI patterns | [Building Effective Agents (Anthropic)](https://www.anthropic.com/research/building-effective-agents) | 30 min |
| Structured output from LLMs | [Instructor library docs](https://python.useinstructor.com) | 1 hour |
| Prompt engineering | [Prompt Engineering Guide](https://www.promptingguide.ai) | 2 hours |
| concurrent.futures | [Python docs](https://docs.python.org/3/library/concurrent.futures.html) | 30 min |
| Pydantic v2 | [Pydantic docs](https://docs.pydantic.dev/latest/) | 1 hour |
| Rich library | [Rich docs](https://rich.readthedocs.io) | 30 min |
| Typer | [Typer docs](https://typer.tiangolo.com) | 30 min |
| GitHub API | [GitHub REST API docs](https://docs.github.com/en/rest) | 1 hour |
| Mamba architecture | [NVIDIA Nemotron blog](https://developer.nvidia.com/blog/introducing-nemotron-3-super-an-open-hybrid-mamba-transformer-moe-for-agentic-reasoning/) | 30 min |
| AgentOps / multi-agent trends | [KDnuggets MLOps 2026](https://www.kdnuggets.com/5-cutting-edge-mlops-techniques-to-watch-in-2026) | 30 min |
| Python packaging (PEP 621) | [Python Packaging User Guide](https://packaging.python.org/en/latest/) | 30 min |
| uv documentation | [uv docs](https://docs.astral.sh/uv/) | 30 min |
| ruff documentation | [ruff docs](https://docs.astral.sh/ruff/) | 30 min |
