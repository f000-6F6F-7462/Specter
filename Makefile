.DEFAULT_GOAL := help

COMPOSE := docker compose -f deploy/compose.base.yaml
# The services plus Specter's own processes, all in Docker.
COMPOSE_STACK := docker compose -f deploy/compose.base.yaml -f deploy/compose.yaml
# The development settings keep data, secrets and models inside the repository.
DEVELOPMENT_CONFIG := config/specter.dev.yaml
SPECTER := uv run specter --config $(DEVELOPMENT_CONFIG)

.PHONY: help setup install lock upgrade-dependencies format lint type-check test \
	test-integration test-hardware test-models check ci contracts services-up services-down models migrate \
	up down logs status api-token run-all run-api run-camera-manager run-camera run-detector clean clean-development-data require-uv

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

test-hardware: require-uv  ## Run tests that need an accelerator (CUDA, TensorRT)
	uv run pytest -m hardware

test-models: require-uv  ## Run tests on the real models (needs make models)
	uv run pytest -m models

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

models:  ## Export and download every model into ./models (Docker; torch stays in the container)
	docker build --tag specter-model-export deploy/models
	docker run --rm \
		--volume "$(CURDIR)/models:/models" \
		--volume "$(CURDIR)/src/specter/inference/model_manifest.yaml:/manifest.yaml:ro" \
		specter-model-export

migrate: require-uv  ## Apply pending database migrations to the development database
	$(SPECTER) migrate

# One shell runs the three long-lived processes, so Ctrl+C reaches all of them and each stops
# cleanly. Camera processes are not listed: the camera manager starts them. A process that dies is
# not restarted; run it again with its own run-* target.
run-all: require-uv services-up migrate  ## Start the services, then the API, camera manager and detector; Ctrl+C stops them
	@trap 'kill $$(jobs -p) 2>/dev/null; wait' INT TERM; \
	$(SPECTER) api & \
	$(SPECTER) camera-manager & \
	$(SPECTER) detector & \
	wait

run-api: require-uv migrate  ## Run the local HTTP API with the development settings
	$(SPECTER) api

run-camera-manager: require-uv migrate  ## Run the camera manager with the development settings
	$(SPECTER) camera-manager

run-camera: require-uv  ## Run one camera process: make run-camera CAMERA_ID=<id>
	$(if $(CAMERA_ID),,$(error CAMERA_ID is required: make run-camera CAMERA_ID=front_door))
	$(SPECTER) camera --camera-id $(CAMERA_ID)

run-detector: require-uv migrate  ## Run the detector with the development settings
	$(SPECTER) detector

##@ Docker

# The alternative to the run-* targets: every process runs in a container, restarts when it
# crashes, and needs no Python on the machine. Do not run it together with run-all or run-api,
# which use the same ports. Models must exist (make models).
up:  ## Build and start everything in Docker: services, API, camera manager, detector
	$(COMPOSE_STACK) up -d --build

down:  ## Stop everything that make up started (data is kept)
	$(COMPOSE_STACK) down

logs:  ## Follow the logs of every container
	$(COMPOSE_STACK) logs --follow --tail 100

status:  ## Show the state of every container
	$(COMPOSE_STACK) ps

api-token:  ## Print the API token of the containerized API
	@$(COMPOSE_STACK) exec -T api cat /etc/specter/api.token

##@ Maintenance

clean:  ## Remove tool caches
	rm -rf .pytest_cache .mypy_cache .ruff_cache

clean-development-data:  ## Delete the development database, evidence, images and secrets
	rm -rf .dev

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
