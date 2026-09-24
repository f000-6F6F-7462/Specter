# Agent guide: Specter + FaceAlert

Always loaded: keep it short and current.

## Workspace
- **Specter** (repo root, Python 3.12+, uv): headless edge vision engine. Reads RTSP cameras, detects, tracks and identifies people and objects (face, appearance re-id, zone/line rules), raises alerts with a JPEG snapshot. No UI and no users: an app server drives it over the HTTP API (FastAPI, `127.0.0.1:8000`, one Bearer token) and consumes NATS JetStream events (`:4222`). Docker services: NATS, Qdrant `:6333`, go2rtc `:1984`/`:8554`/`:8555`.
- **FaceAlert "fa"** (`integrations/fa/`): the app server and web UI. A git submodule whose `server/` (fa-server) and `client/` (fa-client) are nested submodules with their own repos and branches: commit inside them, not in Specter.
- **Active work**: connecting fa to Specter through an anti-corruption layer in fa-server. Plan: `integration_plan_fa_specter.md` (v2, ~1,500 lines: grep `^## Phase` and read one phase). `implementation_plan_ספרטוקר_אינטגרציה.md` is the superseded v1: skip it.

## Specter code map (`src/specter/`)
`command_line.py`: `specter [--config FILE] api | camera-manager | camera --camera-id ID | detector | migrate` (or `$SPECTER_CONFIG_FILE`).

| Package | Role, key modules |
|---|---|
| `api/` | `main.py:create_app`; `routers/<resource>.py`; `schemas.py` shared bodies; `ownership.py` owner scoping; `notifications.py` publishes `configuration.changed` |
| `camera/` | One process per camera: `stream_reader` → `scene_analyzer` (tracks, rules) → `person_identifier` → `alert_publisher`; `detector_client` |
| `camera_manager/` | `supervisor` (spawns/restarts cameras, publishes status), `go2rtc` (registers streams), `alert_recorder` (NATS alert → DB), `evidence_retention` |
| `detector/` | Serves models to every camera: `batcher` (object detection), `identification` (face/appearance embeddings), `enrollment` (reference image → Qdrant) |
| `vision/` | Pure logic, no I/O: `sampling`, `tracking`, `rules`, `best_shot`, `quality`, `identity_matching` |
| `inference/` | SCRFD, ArcFace, YOLO26, OSNet; `onnxruntime_backend`, `ncnn_backend`; `model_store` + `model_manifest.yaml` |
| `messaging/` | `messages.py` = the NATS contract (pydantic); `subjects.py`; `streams.py` (streams, consumers, KV); `message_bus.py` |
| `storage/` | SQLite via peewee: `tables.py`, one module per resource, `migrations/NNNN_*.py`; `vector_index.py` (Qdrant); `evidence.py`; `credentials.py` |
| `entities/` | Domain model: frozen slotted dataclasses, validated in `__post_init__` |
| `frame_transport/` | Shared-memory frames and detector request/reply |
| `config/` | `settings.py` (pydantic-settings), `loading.py` (YAML + env) |
| `core/` | `errors`, `identifiers`, `logging`, `shutdown` |

Every module starts with a one-line docstring: grep `^"""\S` in `src/specter/**/*.py` for a file-level map in one call. Tests mirror the tree under `tests/unit/`; also `tests/integration/`, `tests/models/`.

Where to change what:
- Endpoint: `api/routers/<resource>.py` → `storage/<resource>.py`; announce with `publish_configuration_change`; regenerate `contracts/openapi.json`; update `docs/integration/http-api.md`.
- NATS message: `messaging/messages.py` (+ `subjects.py`, `streams.py`); regenerate `contracts/jsonschema/` (never hand-edit); update `docs/integration/nats-events.md`.
- DB schema: `storage/tables.py` + new `storage/migrations/000N_<change>.py`.

## Specter contract (what fa uses)
- HTTP: `Authorization: Bearer <token>` (Docker: `deploy/secrets/api.token`; from source: `.dev/secrets/api.token`); OpenAPI at `/docs` and `contracts/openapi.json`. Everything is under `/owners/{owner_id}/` except `/health`: `cameras` (+ `/start`, `/stop`; `metadata`), `cameras/{id}/zones|rules|live`, `watchlists/{id}/targets` (multipart photos, `/images/{id}` download), `enrollment-batches/{id}`, `alerts` (merged list, `/summary`, `/identity-matches`, `/rules`, `/{id}`, `/acknowledge`, `/resolve`, `/snapshot`; `camera_id` repeatable).
- NATS: `specter.owners.{owner}.cameras.{camera}.{match_confirmed|rule_triggered|status_changed}` and `specter.owners.{owner}.{enrollment.status_changed|configuration.changed}`. Streams `EVENTS` (7 days), `CAMERA_STATUS`, `CONFIGURATION`; KV `camera_health` (10 s TTL). Internal, never consume: `ENROLLMENT_JOBS`, `specter.detector.*`. JSON Schemas: `contracts/jsonschema/`.
- Gotchas: `start` only requests, so wait for `status_changed`; enrollment is async; at most one identity alert per target and camera per cooldown (30 s); a just-published alert can 404 on `GET` briefly, so retry; zone and rule coordinates are 0–1 fractions.

## Commands (Windows, pwsh: `make` is not installed, so use these)
- Setup: `uv sync`
- Lint: `uv run ruff format --check . ; uv run ruff check .` (fix: `uv run ruff format . ; uv run ruff check --fix .`)
- Types: `uv run python -m mypy` (`uv run mypy` fails here with "Access is denied")
- Tests: `uv run python -m pytest [path]` runs unit tests only (`uv run pytest` is also denied); 14 POSIX-only tests fail on Windows; `-m integration` needs NATS, Qdrant, FFmpeg; `-m models` needs `./models`.
- Contracts: `uv run python -m specter.messaging.schemas contracts/jsonschema ; uv run python -m specter.api.openapi contracts/openapi.json` (drift tests fail otherwise)
- Services: `docker compose -f deploy/compose.base.yaml up -d`; whole stack: add `-f deploy/compose.yaml`, then `up -d --build`.
- From source: `uv run specter --config config/specter.dev.yaml migrate`, then the same with `api`, `camera-manager`, `detector`.
- fa-server: `npm run dev|test|typecheck`; fa-client: `npm run dev|build|lint`.

## Conventions
Specter (ruff and strict mypy enforce the rest; lines ≤ 100):
- Google-style docstrings (ruff `D`, not in tests); comments say why, not what.
- Full-word names; booleans `is_*`; constants carry units (`*_SECONDS`, `*_BYTES`).
- Entities are frozen dataclasses; NATS messages and API bodies are pydantic.

fa-server (Express 5, ESM, TypeScript non-strict, vitest):
- Imports end in `.js`. A new path alias goes in both `tsconfig.json` `paths` and `vitest.config.ts` `resolve.alias`.
- Feature folder: `*Routes` → `*Controller` → `*Service`; ports in `domain/`, adapters in `infrastructure/` with `tokens.ts` (`Symbol.for("fa.<feature>.<Name>")`), registered in `src/core/di.ts` (tsyringe).
- Auth middleware: `authenticateToken`, `requireRole([...])`. Data: Supabase (users, cameras), MongoDB (events, GridFS photos), Qdrant (vectors), Socket.IO (live notifications).
- Specter ACL in `server/src/specter/` (typed client, NATS stream, catalog); every route under `/api`. After Specter contract changes: `npm run specter:contracts`. Camera access: `CameraService.getAccessibleCamera`/`getManageableCamera`; live WS is relayed in `src/live/` (ticket, `upgrade` handler).

fa-client: React 19, Vite, Tailwind, TanStack Query; API calls in `src/services/` and `src/lib/api-client.ts`; UI primitives in `src/components/ui/`.

## Token rules
- Don't read or search: `node_modules/`, `.venv/`, `dist/`, `models/`, `.dev/`, `__pycache__/`, `uv.lock`, `package-lock.json`, `*.backup`.
- Never open or print secrets (`סודות.txt`, `.env*`, `.dev/secrets/`); refer to them by path.
- Docs are 5–22 KB each: grep the headings and read one section. `docs/architecture.md` (processes, flows, data model), `docs/integration/{app-integration-guide,http-api,nats-events,live-video}.md`, `docs/deployment.md`.
- Keep replies short; don't echo unchanged code.
