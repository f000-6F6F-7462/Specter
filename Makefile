.DEFAULT_GOAL := help

COMPOSE := docker compose -f deploy/compose.base.yaml

.PHONY: help setup install lock upgrade-dependencies format lint type-check test \
	test-integration test-hardware check ci contracts services-up services-down run-api \
	run-camera-manager run-camera run-detector clean require-uv

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

contracts: require-uv  ## Regenerate the message JSON Schemas in contracts/jsonschema
	uv run python -m specter.messaging.schemas contracts/jsonschema

##@ Run

services-up:  ## Start NATS, Qdrant and go2rtc in Docker
	$(COMPOSE) up -d

services-down:  ## Stop NATS, Qdrant and go2rtc
	$(COMPOSE) down

run-api: require-uv  ## Run the local HTTP API
	uv run specter api

run-camera-manager: require-uv  ## Run the camera manager
	uv run specter camera-manager

run-camera: require-uv  ## Run one camera process: make run-camera CAMERA_ID=<id>
	$(if $(CAMERA_ID),,$(error CAMERA_ID is required: make run-camera CAMERA_ID=front_door))
	uv run specter camera --camera-id $(CAMERA_ID)

run-detector: require-uv  ## Run the detector
	uv run specter detector

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
