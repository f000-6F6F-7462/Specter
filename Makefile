.DEFAULT_GOAL := help

# Every Specter process runs in Docker. Running them from source is still possible with the
# `specter` CLI directly; see README.md. It has no make targets, to keep this file to the
# commands that are actually used.
COMPOSE := docker compose -f deploy/compose.base.yaml -f deploy/compose.yaml

.PHONY: help setup format lint test check contracts \
	models up down logs status api-token api-token-file clean clean-dev require-uv

##@ Setup

setup: require-uv  ## Prepare a fresh clone: dependencies and git hooks
	uv sync
	uv run pre-commit install

##@ Quality

format: require-uv  ## Format code and apply safe lint fixes
	uv run ruff format .
	uv run ruff check --fix .

lint: require-uv  ## Check formatting, lint rules and types
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy

test: require-uv  ## Run unit tests
	uv run pytest

check: lint test  ## Every static check, then the unit tests

contracts: require-uv  ## Regenerate the NATS JSON Schemas and the HTTP OpenAPI document in contracts/
	uv run python -m specter.messaging.schemas contracts/jsonschema
	uv run python -m specter.api.openapi contracts/openapi.json

##@ Run

models:  ## Export and download every model into ./models (Docker; torch stays in the container)
	docker build --tag specter-model-export deploy/models
	docker run --rm \
		--volume "$(CURDIR)/models:/models" \
		--volume "$(CURDIR)/src/specter/inference/model_manifest.yaml:/manifest.yaml:ro" \
		specter-model-export

# Every process runs in a container, restarts when it crashes, and needs no Python on the machine.
# Models must exist (make models).
up: deploy/secrets/api.token  ## Build and start everything in Docker: services, API, camera manager, detector
	$(COMPOSE) up -d --build

down:  ## Stop everything that make up started (data is kept)
	$(COMPOSE) down

logs:  ## Follow the logs of every container
	$(COMPOSE) logs --follow --tail 100

status:  ## Show the state of every container
	$(COMPOSE) ps

api-token: deploy/secrets/api.token  ## Print the API token, creating it on first use
	@cat $<

# Provisions the token without printing it, for rollouts that keep secrets out of logs.
api-token-file: deploy/secrets/api.token  ## Create the API token without revealing it

# The directory keeps other users of the device out; the file itself is readable by any uid,
# because the API (uid 10001) and the application server run as different users.
deploy/secrets/api.token:
	@mkdir -p deploy/secrets && chmod 700 deploy/secrets
	@head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n' > $@.partial
	@chmod 444 $@.partial && mv $@.partial $@
	@echo "created $@"

##@ Maintenance

clean:  ## Remove the tool caches (pytest, mypy, ruff)
	rm -rf .pytest_cache .mypy_cache .ruff_cache

# Removes the Docker volumes as well as .dev, because the database lives in a volume whenever
# Specter ran through `make up`. The API token file is kept, so clients stay configured; the
# credentials key inside the volume is not, which is harmless once the database is gone too.
clean-dev:  ## Delete all development data: database, vectors, evidence and NATS state
	$(COMPOSE) down --volumes
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
