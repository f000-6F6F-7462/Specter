.DEFAULT_GOAL := help

.PHONY: help setup install lock upgrade-dependencies format lint type-check test \
	test-integration test-hardware check ci clean require-uv

##@ Setup

setup: install  ## Prepare a fresh clone: dependencies and git hooks
	uv run pre-commit install

install: require-uv  ## Create the environment and install dependencies
	uv sync

lock: require-uv  ## Regenerate uv.lock from pyproject.toml
	uv lock

upgrade-dependencies: require-uv  ## Upgrade all dependencies within their allowed ranges
	uv lock --upgrade
	uv sync

##@ Quality

format: require-uv  ## Format code and apply safe lint fixes
	uv run ruff format .
	uv run ruff check --fix .

lint: require-uv  ## Check formatting and lint rules
	uv run ruff format --check .
	uv run ruff check .

type-check: require-uv  ## Run mypy in strict mode
	uv run mypy

test: require-uv  ## Run unit tests
	uv run pytest

test-integration: require-uv  ## Run tests against real services (nats-server, Qdrant, FFmpeg)
	uv run pytest -m integration

test-hardware: require-uv  ## Run tests that need an accelerator (Hailo, CUDA)
	uv run pytest -m hardware

check: lint type-check test  ## Lint, type-check and unit tests

ci: require-uv  ## Install exactly from uv.lock, then run all checks
	uv sync --locked
	$(MAKE) check

##@ Maintenance

clean:  ## Remove tool caches
	rm -rf .pytest_cache .mypy_cache .ruff_cache

help:  ## Show this help
	@awk 'BEGIN {FS = ":.*##"} \
		/^[a-zA-Z_-]+:.*##/ {printf "  %-22s %s\n", $$1, $$2} \
		/^##@/ {printf "\n%s\n", substr($$0, 5)}' $(MAKEFILE_LIST)

# Fails early with an installation hint instead of a bare "command not found".
require-uv:
	@command -v uv >/dev/null 2>&1 || { \
		echo "uv is not installed: https://docs.astral.sh/uv/getting-started/installation/"; \
		exit 1; \
	}
