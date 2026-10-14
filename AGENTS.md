# Specter agent guide

Scope: this repository and its tracked `dashboard/` submodule. Prefer current source, manifests and
product docs over old plans, cached checkouts or assumptions. Verify paths and Git status before
editing.

## Repository boundaries

- The root repository is **Specter**, a Python 3.12+ edge-vision engine managed with `uv`.
- [`dashboard/`](dashboard/) is a tracked submodule included by `make up`. It contains the dashboard
  application; `dashboard/server/` and `dashboard/client/` are nested submodules.
- Before dashboard work, check `git status` at the root, `dashboard/` and the affected nested
  repository. Make changes/commits in the repository that owns the file. Do not switch branches,
  update submodules or change their pointers unless asked.
- Preserve existing worktree changes. Do not stage unrelated or untracked files.

## System map

Specter has no user interface or user accounts. Its HTTP API handles reads and changes; NATS
JetStream publishes events. The application server owns users and access control.

| Path | Responsibility |
|---|---|
| `src/specter/command_line.py` | `specter` CLI: `api`, `camera-manager`, `camera --camera-id`, `detector`, `agent`, `migrate` |
| `src/specter/agent/` | Edge agent gateway: cloud Socket.IO connection, camera & watchlist sync, offline queue, alert store-and-forward, and WebRTC relay |
| `src/specter/api/` | FastAPI app, routers, schemas, authentication, ownership and notifications |
| `src/specter/camera/` | Camera stream reading, scene analysis, identification and alert publishing |
| `src/specter/camera_manager/` | Camera supervision, go2rtc registration, alert recording and evidence retention |
| `src/specter/detector/` | Batched object detection, identification embeddings and async enrollment |
| `src/specter/vision/` | Sampling, tracking, rules, best-shot selection, quality and identity matching |
| `src/specter/inference/` | Face/object/appearance models, inference backends and model manifest |
| `src/specter/messaging/` | NATS messages, subjects, streams, shared state and schema generation |
| `src/specter/storage/` | SQLite persistence, migrations, evidence, credentials and Qdrant vector index |
| `src/specter/entities/` | Validated domain dataclasses |
| `src/specter/frame_transport/` | Shared-memory frames and detector request/reply |
| `src/specter/config/`, `src/specter/core/` | Settings/loading and shared errors, identifiers, logging and shutdown |
| `tests/unit/` | Unit tests mirroring the source packages |
| `tests/integration/` | Tests requiring services or media tools |
| `tests/models/` | Tests requiring downloaded model files |

Runtime services are NATS, Qdrant and go2rtc (`deploy/compose.infra.yaml`). Specter processes are
`migrate`, `api`, `camera-manager` and `detector` (`deploy/compose.specter.yaml`); the manager starts
one camera process per active camera. `deploy/compose.dashboard.yaml` adds the dashboard, and
`deploy/compose.agent.yaml` adds the edge agent process.

## Change map

- **HTTP API:** update the relevant `src/specter/api/routers/` module and, as needed, shared
  `api/schemas.py`, storage/domain code and tests. Regenerate `contracts/openapi.json` with
  `make contracts`; update [`docs/integration/http-api.md`](docs/integration/http-api.md).
- **NATS:** update `messaging/messages.py` and affected subjects, streams, consumers or shared state.
  Regenerate `contracts/jsonschema/` with `make contracts`—never hand-edit generated contracts—and
  update [`docs/integration/nats-events.md`](docs/integration/nats-events.md).
- **Database:** update `storage/tables.py` and add the next numbered migration under
  `storage/migrations/`. Inspect the migration runner and tests before changing migration behavior.
- **Configuration:** update `config/settings.py` and/or `config/loading.py`; document user-facing
  changes and keep service wiring in `config/specter.dev.yaml` or the relevant Compose file.
  Settings use YAML plus environment variables (`SPECTER_` prefix, `__` nested delimiter).
- **Application integration:** follow the current HTTP and NATS contracts in `contracts/` and
  [`docs/integration/`](docs/integration/). Specter owns camera/vision resources; the application
  server owns user identity and access control.

## Dashboard

Treat `dashboard/` as a separate repository boundary, not as Specter source. Inspect its current
README, nested repository status and `package.json` before relying on structure, runtime versions or
scripts. The current layout has an Express/TypeScript server under `server/` and a React/Vite client
under `client/`; each is independently versioned. Contract sync, when relevant, is run from the
server package with `npm run specter:contracts`.

## Run and validate

The root [`Makefile`](Makefile) and package manifests are authoritative for commands:

- `make setup` prepares Python dependencies and hooks. `make check` runs formatting, lint, types and
  unit tests; focused alternatives are `make lint` and `make test`.
- `make contracts` regenerates HTTP and NATS contracts. `make models` downloads/prepares model files.
- `make engine-up` runs Specter and its services in Docker; `make up` also builds the dashboard.
  `make status`, `make logs` and `make down` inspect or stop the Compose stack; `make down` keeps
  data. Do not run source processes and `make engine-up` together because they share ports.
- For source runs, follow [`docs/running.md`](docs/running.md). Integration and model test
  prerequisites are described in [`pyproject.toml`](pyproject.toml) and the docs.
- For dashboard changes, use the selected nested package's declared scripts (typically server
  `npm run test` / `npm run typecheck`; client `npm run test` / `npm run build` / `npm run lint`).
  Check the package's Node/npm engine requirements first.
- `make clean-dev` deletes development data and Docker volumes. Run it only when explicitly asked.

Other product references: [`README.md`](README.md), [`docs/architecture.md`](docs/architecture.md),
[`docs/deployment.md`](docs/deployment.md), [`docs/running.md`](docs/running.md), and
[`docs/integration/app-integration-guide.md`](docs/integration/app-integration-guide.md).

## Safety and conventions

- Never read, print or copy secret values. Do not inspect `.env*`, `.dev/`, `deploy/secrets/` or
  credential files. Use `config/settings.py` and public docs for setting names; refer to secret files
  by path only.
- Avoid dependency, generated, runtime and model data (`node_modules/`, `.venv/`, `dist/`, `models/`,
  `__pycache__/`, caches and `*.backup`) unless the task explicitly concerns them. Do not modify
  lockfiles unless dependency changes require it.
- Specter uses Ruff formatting/lint, strict mypy, 100-character Python lines and Google-style
  docstrings. Prefer existing helpers and validated entities; comments explain why, not what.
- Follow the selected dashboard package's local conventions. Keep API/NATS docs, generated
  contracts and tests in sync with behavior changes.
- Do not commit unless asked. Never discard another contributor's changes.
