# Custom YAML-Defined Agents

Custom agents let you add domain-specific review expertise without writing Python.
Define a YAML file with a name, system prompt, and optional file patterns, and the
tool creates a fully functional review agent at runtime.

## Quick Start

Create `.cra/agents/django_security.yaml` in your project root:

```yaml
name: django_security
description: Checks Django views for common security issues
system_prompt: |
  You are a Django security reviewer. Focus on:
  - Missing permission checks on views
  - Unescaped user input in templates
  - Raw SQL queries without parameterization
  - Insecure default settings (DEBUG=True, ALLOWED_HOSTS=["*"])
  Report only confirmed issues with file paths and line numbers.
priority: 10
file_patterns:
  - "*.py"
  - "*.html"
```

Run a review and the agent appears automatically:

```bash
cra review --repo owner/repo --pr 42
cra agents  # lists all agents, including custom ones
```

## YAML Schema Reference

| Field            | Type           | Default | Required | Description                                                    |
| ---------------- | -------------- | ------- | -------- | -------------------------------------------------------------- |
| `name`           | `str`          | --      | Yes      | Unique identifier. Must match `^[a-z][a-z0-9_]*$`.            |
| `system_prompt`  | `str`          | --      | Yes      | The LLM system prompt. Must be non-empty.                      |
| `description`    | `str`          | `""`    | No       | Human-readable summary shown in `cra agents` output.           |
| `priority`       | `int`          | `100`   | No       | Deduplication priority (lower number = higher priority). >= 0. |
| `enabled`        | `bool`         | `true`  | No       | Set to `false` to skip loading without deleting the file.      |
| `file_patterns`  | `list[str]`    | `null`  | No       | fnmatch glob patterns. `null` means match all files.           |

Unknown fields are silently ignored (`extra="ignore"`), so YAML files remain
forward-compatible with future schema additions.

## Discovery Order

Agents are loaded from two directories, in order:

1. **Project-local**: `.cra/agents/` relative to the current working directory
2. **User-global**: `~/.cra/agents/` (configurable via `custom_agents_dir` in settings)

Within each directory, files are sorted alphabetically for deterministic load order.
Non-existent directories are silently skipped.

## Override Semantics

Later directories override earlier ones by `name`. The override chain is:

```
built-in agents  <  .cra/agents/  <  ~/.cra/agents/
```

- If a custom agent shares a name with a built-in (e.g. `security`), it replaces
  the built-in and a warning is logged.
- If the same name appears in both directories, the user-global definition wins.

## File Patterns

The `file_patterns` field uses Python's `fnmatch` module for Unix shell-style matching.

| Pattern        | Matches                                |
| -------------- | -------------------------------------- |
| `*.py`         | All Python files                       |
| `*.tsx`        | All TSX files                          |
| `Dockerfile*`  | `Dockerfile`, `Dockerfile.dev`, etc.   |
| `k8s/*.yaml`   | YAML files directly under `k8s/`      |

Rules:

- `null` (omitted) -- the agent runs on every file in the diff.
- Empty list `[]` -- the agent never matches (effectively disabled).
- Matching is per-filename as it appears in the diff (relative path from repo root).
- If **any** filename in the diff matches **any** pattern, the agent runs.

## Priority

When multiple agents produce findings for the same issue, the orchestrator
deduplicates by keeping the finding from the agent with the **lowest** priority
value (highest precedence).

- Built-in agents default to priority `100`.
- Set a lower number (e.g. `10`) to give your custom agent precedence.
- Set a higher number (e.g. `200`) to let built-in agents take precedence.

## Enabling and Disabling Agents

**Disable a custom agent** without deleting its file:

```yaml
name: django_security
enabled: false
system_prompt: "..."
```

**Select agents at review time** with the `--agents` flag:

```bash
cra review --agents security,django_security --repo owner/repo --pr 42
```

**Set default agents** in configuration (environment variable or `.env`):

```bash
CRA_DEFAULT_AGENTS=security,django_security,style
```

When `default_agents` is empty (the default), all enabled agents run.

## Examples

### React Accessibility Agent

```yaml
name: react_a11y
description: Checks React components for WCAG accessibility violations
system_prompt: |
  You are a WCAG 2.1 AA accessibility reviewer for React. Check for:
  - Missing alt text on images
  - Click handlers on non-interactive elements without role/tabIndex
  - Missing aria-label on icon-only buttons
  - Form inputs without associated labels
  - Incorrect heading hierarchy
priority: 50
file_patterns:
  - "*.tsx"
  - "*.jsx"
```

### Kubernetes Manifest Validator

```yaml
name: k8s_validator
description: Validates Kubernetes manifests for production readiness
system_prompt: |
  You are a Kubernetes deployment reviewer. Check for:
  - Missing resource requests/limits
  - Containers running as root
  - Missing liveness/readiness probes
  - Use of :latest image tags
  - Secrets mounted as environment variables instead of volumes
  - Missing PodDisruptionBudget for production workloads
priority: 30
file_patterns:
  - "*.yaml"
  - "*.yml"
```

## Using Custom Agents

```bash
cra agents                                                    # list all agents
cra review --agents security,k8s_validator --repo o/r --pr 1  # run specific agents
```

The `cra agents` output marks custom agents and shows their priority and description.

## Troubleshooting

**Agent not loading**

- Verify the file is in `.cra/agents/` (project) or `~/.cra/agents/` (global).
- File extension must be `.yaml` or `.yml`.
- Run with `--log-level debug` to see discovery and loading logs.

**Invalid YAML syntax**

- The loader skips malformed files with a warning. Check logs for `"skipping invalid agent YAML"`.
- Ensure the file contains a YAML mapping (not a list or scalar).

**Invalid name format**

- Names must match `^[a-z][a-z0-9_]*$` -- lowercase, starts with a letter,
  only letters, digits, and underscores.
- Bad: `Django-Security`, `2fast`, `my agent`. Good: `django_security`.

**Name collision**

- Two custom agents with the same `name` cannot coexist in the same directory.
  The second file (alphabetically) wins across directories.
- Overriding a built-in agent logs a warning. This is intentional but verify
  it is what you want.

**Agent runs but produces no findings**

- Check `file_patterns` -- if no diff file matches, the agent is skipped.
- An empty `file_patterns: []` means the agent never matches anything.
- Review the `system_prompt` for clarity. Vague prompts produce vague results.
