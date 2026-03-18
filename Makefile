.DEFAULT_GOAL := help

.PHONY: help install fmt lint typecheck test check review build publish clean

help: ## Show all available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

install: ## Install all dependencies
	uv sync

fmt: ## Format code with ruff
	uv run ruff format .

lint: ## Lint code with ruff
	uv run ruff check .

typecheck: ## Run mypy type checking
	uv run mypy src/

test: ## Run tests with pytest
	uv run pytest

check: lint typecheck test ## Run lint + typecheck + test

review: ## Run code-review-ai (example usage)
	uv run code-review-ai review

build: clean ## Build sdist and wheel
	uv build

publish: build ## Publish to PyPI (requires PYPI_TOKEN)
	uv publish

clean: ## Remove build artifacts and caches
	rm -rf __pycache__ .mypy_cache .ruff_cache .pytest_cache dist
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
