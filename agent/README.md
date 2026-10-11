# Specter Agent

The agent is a small local gateway for the first MVP. It keeps camera streams on the
customer's network and synchronizes the configured cameras with a local Specter API.

The agent can also send a production heartbeat when its API URL points at the Dashboard
server. Production pairing and remote camera relay are separate steps; a heartbeat does
not expose a camera stream.

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
