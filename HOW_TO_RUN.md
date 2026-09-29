# How to Run Specter & FaceAlert

This is a concise guide to running the entire application stack and its test suites.

## 1. Start the Specter Engine (Docker)
The core backend (API, Camera Manager, Detector, Qdrant, NATS, go2rtc) runs via Docker Compose.
```bash
# From the project root:
docker compose -f deploy/compose.base.yaml -f deploy/compose.yaml up -d --build
```
*Note: Make sure your API token exists at `deploy/secrets/api.token` before starting.*

## 2. Start the Dashboard Server (Node.js)
The middle-tier API connecting the frontend to the Specter engine.
```bash
cd dashboard/server
npm install
npm run dev
```

## 3. Start the Dashboard Client (React)
The web interface.
```bash
cd dashboard/client
npm install
npm run dev
```

---

## How to Run Tests

### Specter Python Tests
```bash
# From the project root:
uv sync
uv run python -m pytest tests/unit
```
*(If you are running from WSL and hit file lock issues with `.venv`, set `UV_PROJECT_ENVIRONMENT=.venv_wsl` before running `uv sync` and `uv run`).*

### Dashboard Server Tests
```bash
cd dashboard/server
npm run test
```

### Dashboard Client Tests
```bash
cd dashboard/client
npm run test
```
