#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f "deploy/secrets/agent.token" ]]; then
  echo "Error: deploy/secrets/agent.token not found." >&2
  exit 1
fi

if [[ ! -f "deploy/secrets/api.token" ]]; then
  echo "Error: deploy/secrets/api.token not found." >&2
  exit 1
fi

export SPECTER_AGENT__BACKEND_URL="${SPECTER_AGENT__BACKEND_URL:-https://api.eyec.live}"
export SPECTER_AGENT__AGENT_TOKEN="$(cat deploy/secrets/agent.token)"
export SPECTER_AGENT__MODE="cloud"
export SPECTER_AGENT__SPECTER_API_URL="${SPECTER_AGENT__SPECTER_API_URL:-http://127.0.0.1:8000}"
export SPECTER_AGENT__OWNER_ID="${SPECTER_AGENT__OWNER_ID:-facealert}"
export SPECTER_AGENT__LOCAL_OWNER_ID="${SPECTER_AGENT__LOCAL_OWNER_ID:-facealert}"
export SPECTER_SECURITY__API_TOKEN_FILE="deploy/secrets/api.token"
export SPECTER_SERVICES__NATS_URL="${SPECTER_SERVICES__NATS_URL:-nats://127.0.0.1:4222}"

echo "Starting Specter Edge Agent connected to $SPECTER_AGENT__BACKEND_URL..."
exec uv run specter agent
