# Specter Production Agent Connection

## Purpose

Connect a customer's local Specter installation to the Production dashboard through
an outbound HTTPS connection. The local installation must not require port forwarding,
public RTSP exposure, or inbound connections to the customer's network.

Target architecture:

```text
Local cameras
    |
    v
Local Specter + Agent
    |
    | outbound HTTPS
    v
https://api.eyec.live
    |
    v
Production Dashboard + Supabase
```

The long-term goal is for an authorized user to access only cameras belonging to
their account from anywhere. The current implementation is the identity and
connectivity foundation; remote video is intentionally a later phase.

## Current implementation

### Implementation update from the latest commits

The Dashboard server now includes the following Agent-related work:

| Commit | Implemented capability |
|---|---|
| `df210a5` | `/agent` Socket.IO namespace and Phase 1 camera sync API |
| `81adcad` | WebRTC signaling relay and Cloudflare TURN configuration |
| `97191bc` | Watchlist and target sync endpoints/events |
| `e0b91c3` | Cloud camera catalog and Agent camera sync ingestion |
| `51ede34` | Guard against empty initial camera sync payloads |
| `9bde8e3` | Register Agent Socket.IO listeners synchronously |
| `3fd5fbd` | Agent alert store and hybrid alert repository with snapshots |

These commits are present in `dashboard/server` at `v0.15.0`.
The root `agent/agent.py` now includes a Socket.IO transport alongside the legacy
HTTP heartbeat transport. The new `socket-connect` command authenticates against
`/agent`, sends heartbeats, publishes the local camera catalog, and applies valid
cloud camera definitions to the local API.

### Production API

The dashboard server contains Agent management and heartbeat endpoints:

- `POST /api/agents` creates an Agent for the authenticated user.
- `GET /api/agents` lists Agents visible to the authenticated user.
- `DELETE /api/agents/:agent_id` revokes an Agent.
- `POST /api/agent/heartbeat` authenticates an Agent token and records a heartbeat.

User-facing management endpoints require the normal user JWT. The heartbeat endpoint
uses a separate Agent token and does not use the user's JWT.

Relevant implementation:

- `dashboard/server/src/agents/agentRoutes.ts`
- `dashboard/server/src/agents/agentController.ts`
- `dashboard/server/src/agents/agentService.ts`
- `dashboard/server/src/agents/agentSchemas.ts`

### Database

The Production Supabase database has the `public.agents` table. The source SQL is:

`dashboard/docs/db/supabase/agents_table.sql`

The table stores:

- Agent ID
- Owner user ID
- Display name
- SHA-256 hash of the Agent token
- Optional Agent version
- Creation time
- Last heartbeat time
- Revocation time

The raw token is never stored in the database.

### Local Agent

The Python Agent is located at `agent/agent.py`. It currently supports:

```text
sync       Synchronize configured cameras with a local Specter API
start      Start configured local cameras
status     Show local camera status
heartbeat  Send one heartbeat to Production
connect    Keep sending heartbeats to Production
socket-connect  Maintain the Socket.IO Agent connection and synchronize cameras
```

The Production URL is configured with:

```bash
SPECTER_AGENT_API_URL=https://api.eyec.live
```

The heartbeat interval defaults to 30 seconds and can be changed with:

```bash
--heartbeat-interval 10
```

Connection failures use exponential backoff up to 60 seconds. The process stops
with `Ctrl+C`.

The currently running local process is the legacy HTTP heartbeat Agent. The
Socket.IO transport is implemented but requires a separate Production Agent token
file before it can replace the legacy process.

## Verified Production state

The following flow was completed successfully:

1. Authenticated with the Production user account.
2. Created an Agent named `Local Specter Gateway`.
3. Stored its token locally without recording the token value.
4. Set the token file permissions to `600`.
5. Sent multiple Production heartbeats.
6. Confirmed the Agent appeared as `online` through `GET /api/agents`.

The current Agent record has a Production-generated UUID and is not revoked.
Credentials and tokens are deliberately not recorded in this document.

## Running the current connection

From the repository root:

```bash
SPECTER_AGENT_API_URL=https://api.eyec.live \
  uv run --with httpx python -u agent/agent.py \
  --config agent/config.example.json \
  --heartbeat-interval 30 \
  connect
```

Expected output:

```text
Cloud heartbeat accepted
Cloud heartbeat accepted
```

The Agent is considered online when its last heartbeat is within the server's
90-second online window. Stopping the process eventually changes the status to
`offline`.

For the Socket.IO transport, configure separate local and cloud token files:

```json
{
  "api_token_file": "deploy/secrets/api.token",
  "cloud_token_file": "deploy/secrets/agent.token"
}
```

Run it with:

```bash
uv run --with httpx --with 'python-socketio[client]' \
  python agent/agent.py --config agent/config.json socket-connect
```

## Security model

- The Agent initiates the connection; the cloud does not connect to customer LANs.
- Production user endpoints require a user JWT.
- Agent heartbeat requires a dedicated Agent token.
- Agent tokens are generated with cryptographically secure random bytes.
- Only a SHA-256 token hash is stored in Supabase.
- The raw token is returned only during Agent creation.
- Revocation prevents future heartbeat authentication.
- The local token file must be readable only by its owner (`chmod 600`).
- Tokens, passwords, JWTs, camera credentials, and private URLs must never be logged
  or committed.
- Camera access must always be filtered by the Agent owner and existing dashboard
  authorization rules.

## What is not implemented yet

The current connection proves Agent identity and liveness only. It does not yet:

- Synchronize cameras from the local Agent to Production.
- Create or update cloud camera records.
- Upload snapshots or detection events.
- Relay alerts from the local installation.
- Open a reverse WebSocket tunnel.
- Relay Live video.
- Implement WebRTC.
- Provide a dashboard UI for creating and pairing Agents.
- Run the Agent as a managed system service.
- Replace the current synchronous Socket.IO transport with an async process if
  concurrent event and media forwarding requires it.
- Add the local NATS consumer that forwards camera status and alerts.
- Add the SQLite store-and-forward queue for alerts and snapshots.
- Add the Agent-side WebRTC SDP and ICE relay.
- Add the Agent-side watchlist and target reconciliation.

Therefore the current data path is:

```text
Agent -> HTTPS heartbeat -> Production -> Online/Offline status
```

It is not yet:

```text
User -> Production -> local camera Live stream
```

## Recommended next phases

### Phase 1: Agent lifecycle hardening

- Add a dashboard Agents page.
- Show the raw token exactly once after creation.
- Add copy/download instructions without logging the token.
- Add a revoke action and confirmation.
- Add a clear pairing status.
- Add structured Agent version and platform information.
- Add a systemd/Docker service definition for the local Agent.
- Add tests for token creation, invalid tokens, revoked tokens, ownership, and
  online/offline transitions.

### Phase 2: Camera and event synchronization

- Define a cloud camera identity linked to `agent_id` and `owner_id`.
- Add authenticated Agent endpoints for camera registration and status updates.
- Reuse existing camera assignment rules for tenant isolation.
- Send snapshots or alert metadata over HTTPS.
- Make retries idempotent so reconnects cannot create duplicate cameras or events.
- Add retention and deletion behavior for uploaded evidence.

### Phase 3: Remote Live

Live video needs a bidirectional transport, not a heartbeat. Candidate designs:

1. Agent WebSocket connection to Production, with authorized stream commands.
2. WebRTC using a signaling channel and a relay/TURN service when direct peer
   connectivity is unavailable.
3. A short-lived stream ticket issued only after dashboard authorization.

Required rules:

- Verify the requesting user's access to the camera before opening a stream.
- Verify that the camera belongs to the same tenant as the Agent.
- Never expose the customer's RTSP URL to the browser.
- Never accept arbitrary camera commands from an unauthenticated client.
- Close streams on token revocation, authorization changes, or timeout.
- Log stream lifecycle metadata without logging credentials or media contents.

## Operational notes

- Node.js and npm are installed through NVM.
- Node 22 is the configured NVM default.
- New login shells expose:

  ```text
  node v22.23.3
  npm 10.9.9
  ```

- The Production Agent token is stored outside Git in `deploy/secrets/api.token`.
- The local webcam mapping in `deploy/compose.infra.yaml` is machine-specific and
  is not the Production connection design.

## Acceptance criteria for the next milestone

The next milestone is complete when:

1. A user can create an Agent from the dashboard.
2. The token is shown once and can be paired locally.
3. The Agent reconnects automatically after a network interruption.
4. The dashboard shows accurate online/offline status.
5. A camera registered by an Agent is visible only to authorized users of that
   tenant.
6. Snapshot/event delivery is authenticated, retried, and idempotent.
7. Live access is not marked complete until the reverse relay is implemented and
   tested end to end.
