# Specter Agent

Specter provides edge agent capability in two forms:

1. **Native Integrated Agent (`specter agent` / `src/specter/agent/`)**: The full production-grade
   edge gateway included directly in Specter. It handles bidirectional camera sync, watchlist & target
   sync for autonomous offline face identification, real-time alert forwarding with an offline SQLite
   store-and-forward queue (`agent_offline_queue.sqlite3`), and WebRTC signaling relay with Cloudflare
   TURN/STUN traversal. Run via `deploy/compose.agent.yaml` or `specter agent`.
2. **Standalone Helper Script (`agent/agent.py`)**: A lightweight Python script used for testing,
   manual camera sync, heartbeat verification, and exploratory agent workflows.

## Requirements

- Python 3.12+
- A running local Specter stack (`make engine-up` or `docker compose`)
- A local API token created by the Specter stack
- A cloud agent pairing token (from `POST /api/agents` on the cloud dashboard)

## Configure

Copy the example configuration and edit the camera URLs and credentials:

```bash
cp agent/config.example.json agent/config.json
```

The configuration supports any number of cameras. Credentials are read from the local
file and are never printed by the agent.

`api_token_file` is the local Specter API token. `cloud_token_file` is the token created
when pairing the Agent in Production. Keep these files separate.

## Run

From the repository root:

```bash
uv run --with httpx python agent/agent.py --config agent/config.json sync
uv run --with httpx python agent/agent.py --config agent/config.json start
```

Use `status` to verify the cameras:

```bash
uv run --with httpx python agent/agent.py --config agent/config.json status
```

The local Specter API must be running at `http://127.0.0.1:8000` unless
`SPECTER_AGENT_API_URL` is set.

To verify a paired production agent token:

```bash
SPECTER_AGENT_API_URL=https://api.example.com \
  uv run --with httpx python agent/agent.py --config agent/config.json heartbeat
```

To keep the Agent connected to Production and refresh its online status:

```bash
SPECTER_AGENT_API_URL=https://api.eyec.live \
  uv run --with httpx python agent/agent.py \
  --config agent/config.json connect
```

To use the Production Socket.IO connection and camera synchronization:

```bash
uv run --with httpx --with 'python-socketio[client]' \
  python agent/agent.py --config agent/config.json socket-connect
```

Set `SPECTER_AGENT_CLOUD_URL` to use a different Dashboard URL. The default is
`https://api.eyec.live`.

The default heartbeat interval is 30 seconds. Set `SPECTER_AGENT_HEARTBEAT_INTERVAL`
or pass `--heartbeat-interval` to change it. The Agent retries failed connections with
exponential backoff and stops with `Ctrl+C`.

## Running the Native Integrated Specter Agent

For full production features (automated NATS alert ingestion, persistent SQLite queue, WebRTC relay with STUN/TURN, and watchlist sync), run the integrated `specter agent`:

From source:
```bash
SPECTER_AGENT__MODE=cloud \
SPECTER_AGENT__BACKEND_URL=https://api.eyec.live \
SPECTER_AGENT__AGENT_TOKEN=<your-agent-token> \
uv run --env-file .env specter --config config/specter.dev.yaml agent
```

With Docker Compose:
```bash
docker compose --env-file .env \
  -f deploy/compose.infra.yaml \
  -f deploy/compose.specter.yaml \
  -f deploy/compose.agent.yaml up -d
```

