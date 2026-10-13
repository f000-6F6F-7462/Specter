# Specter Agent

The agent is a small local gateway for the first MVP. It keeps camera streams on the
customer's network and synchronizes the configured cameras with a local Specter API.

The agent can also connect to Production through the authenticated Socket.IO `/agent`
namespace. This keeps the connection outbound and supports camera catalog synchronization.

## Requirements

- Python 3.12+
- A running local Specter stack (`make engine-up`)
- A local API token created by the Specter stack

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
