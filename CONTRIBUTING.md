# Contributing to Code Review AI

Thank you for your interest in contributing! This guide covers the process for submitting changes.

## Getting Started

```bash
git clone https://github.com/minhtribk12/code-review-ai.git
cd code-review-ai
make install   # requires uv
make check     # run fmt + lint + typecheck + tests
```

## Development Workflow

1. **Fork** the repository and clone your fork
2. **Create a branch** from `main`: `git checkout -b feat/your-feature`
3. **Make changes** -- follow the conventions below
4. **Run checks**: `make check` (format, lint, typecheck, tests)
5. **Commit** using [Conventional Commits](#commit-messages)
6. **Push** and open a Pull Request against `main`

## Branch Protection

`main` is protected:
- PRs require at least 1 approving review
- Status checks (`test`, `lint`) must pass
- Stale reviews are dismissed on new pushes
- All conversations must be resolved before merge
- Linear history required (no merge commits)
- Force pushes and deletions are blocked
- Admin rules are enforced (no bypass)

## Code Conventions

- **Python 3.12+** with strict type annotations on all function signatures
- **Ruff** for linting and formatting (configured in `pyproject.toml`)
- **Mypy** in strict mode for type checking
- **PEP 8** naming: `snake_case` functions/variables, `PascalCase` classes
- Prefer early returns and guard clauses over deep nesting
- No `Any` without a justifying comment
- Functions under 30 lines preferred; over 50 lines should be split

## Commit Messages

Format: `type(scope): description`

Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`

```
feat(config): add YAML-based config store
fix(keys-panel): sync not updating display after write
docs: update configuration guide for YAML migration
```

Body required for `feat`/`fix`/`refactor` -- explain **why**, not just what.

## Testing

```bash
make test           # run pytest with coverage
make test-fast      # skip coverage
```

- Every new feature and bugfix must include tests
- Test behavior and contracts, not implementation details
- Mock external dependencies (HTTP, DB), not internal methods
- Tests must be deterministic -- no sleep, no real network calls

## Pull Request Guidelines

- Keep PRs focused -- one logical change per PR
- Fill in the PR template (summary, test plan, checklist)
- Add tests for new functionality
- Update documentation if user-facing behavior changes
- Ensure all CI checks pass before requesting review

## Reporting Issues

Use [GitHub Issues](https://github.com/minhtribk12/code-review-ai/issues) with the appropriate template:
- **Bug report** -- for unexpected behavior
- **Feature request** -- for new functionality

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
