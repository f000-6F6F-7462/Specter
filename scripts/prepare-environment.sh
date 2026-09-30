#!/usr/bin/env bash
# Prepares and checks what every way of starting the application needs: Docker, the models, the
# .env file and Specter's API token. Creates what is missing and stops on what must be filled in by
# hand. The Makefile runs it before `up` and `engine-up`.
#
# Usage:
#   scripts/prepare-environment.sh               # Specter only
#   scripts/prepare-environment.sh --dashboard   # also what the dashboard (fa-server) requires

set -euo pipefail

ROOT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIRECTORY/.env"
API_TOKEN_FILE="$ROOT_DIRECTORY/deploy/secrets/api.token"
DASHBOARD_REQUIRED_KEYS="SUPABASE_URL SUPABASE_KEY JWT_SECRET"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
fail() {
  printf '\033[1;31merror:\033[0m %s\n' "$*" >&2
  exit 1
}

random_secret() { head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n'; }

# Reads KEY from a dotenv file, without quotes, or prints nothing.
env_value() {
  sed -n "s/^$2=//p" "$1" | tail -n 1 | sed -e 's/^["'\'']//' -e 's/["'\'']$//'
}

set_env_value() {
  local file="$1" key="$2" value="$3"
  awk -v key="$key" -v value="$value" \
    'index($0, key "=") == 1 { print key "=" value; next } { print }' "$file" >"$file.partial"
  mv "$file.partial" "$file"
}

check_docker() {
  command -v docker >/dev/null 2>&1 || fail "docker is not installed"
  docker info >/dev/null 2>&1 || fail "the Docker daemon is not running (start Docker Desktop)"
  docker compose version >/dev/null 2>&1 || fail "the docker compose plugin is missing"
}

check_models() {
  if [ -z "$(ls -A "$ROOT_DIRECTORY/models" 2>/dev/null)" ]; then
    fail "models/ is empty; run 'make models' first"
  fi
}

ensure_env_file() {
  if [ ! -f "$ENV_FILE" ]; then
    cp "$ROOT_DIRECTORY/.env.example" "$ENV_FILE"
    set_env_value "$ENV_FILE" JWT_SECRET "$(random_secret)"
    info "created .env from .env.example, with a generated JWT_SECRET"
  fi
}

# Same as `make api-token-file`: the directory keeps other users out, the file is readable by the
# API container (uid 10001) and by fa-server.
ensure_api_token() {
  if [ ! -s "$API_TOKEN_FILE" ]; then
    mkdir -p "$(dirname "$API_TOKEN_FILE")"
    chmod 700 "$(dirname "$API_TOKEN_FILE")"
    random_secret >"$API_TOKEN_FILE.partial"
    chmod 444 "$API_TOKEN_FILE.partial"
    mv "$API_TOKEN_FILE.partial" "$API_TOKEN_FILE"
    info "created deploy/secrets/api.token"
  fi
}

check_dashboard_values() {
  local missing_keys="" key value
  for key in $DASHBOARD_REQUIRED_KEYS; do
    value="$(env_value "$ENV_FILE" "$key")"
    case "$value" in
      "" | your-* | your_*) missing_keys="$missing_keys $key" ;;
    esac
  done
  [ -z "$missing_keys" ] || fail "fill in .env:$missing_keys"
}

check_docker
check_models
ensure_env_file
ensure_api_token
if [ "${1:-}" = "--dashboard" ]; then
  check_dashboard_values
fi
