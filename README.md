# Specter Vision Engine

Real-time object & face detection core engine — headless, hexagonal architecture,
Python 3.14. Runs natively on a single Apple Silicon Mac (for MPS/CoreML inference)
with Docker Compose for backing infra; a CPU-only Docker image covers CI and non-Mac
machines.

## Three processes, one package

| Command | Responsibility |
| --- | --- |
| `specter api` | REST only — FastAPI/Uvicorn |
| `specter ingest` | Stream supervisor — GStreamer capture, detection, tracking, matching |
| `specter enroll` | Consumes enrollment jobs, computes reference embeddings |

`specter` is a thin dispatcher (`src/specter/entrypoints/cli.py`) over the three
standalone scripts (`specter-api`, `specter-ingest`, `specter-enroll`), all installed
as console scripts by `pyproject.toml`.

## Local development (native, MPS)

```bash
make venv && make install
make up                 # postgres, redis, qdrant, minio — docker/docker-compose.yml
make migrate
make run-api             # or: make run-ingest / make run-enroll
```

Copy `.env.example` to `.env` and adjust `SPECTER_*` settings first — in particular
`SPECTER_BUS` / `SPECTER_BLOB` / `SPECTER_VECTORS` (`memory` vs the real backend) and
`SPECTER_MODELS__DETECTOR__DEVICE` (`mps` on Apple Silicon).

## Docker (CPU-only, CI / non-Mac)

```bash
make up-app              # builds docker/Dockerfile, runs migrate, then api+ingest+enroll
curl http://localhost:8000/health
make down-app
```

The image never runs migrations on your host database implicitly outside of this
profile — `docker/docker-compose.yml`'s `migrate` service is scoped to the `app`
profile and runs once (`alembic upgrade head`) before the other containers start.

Inference here is always CPU (`onnxruntime`'s `CPUExecutionProvider`, `torch+cpu`)

## Testing

```bash
make test                # pytest -m 'not integration' — fakes/in-memory adapters only
make up && make test-integration   # -m integration — real Redis/MinIO/Qdrant/Postgres
```

`.github/workflows/ci.yml` runs the same `make test` gate on GitHub-hosted runners,
plus a `docker` job that builds the image, runs the full gate *inside* it (same
CPU/Linux env the image ships), and brings up `--profile app` for a `/health` smoke
test.

## Layout

```
src/specter/
|- core/            settings, clock, ids, logging, errors, DI composition root
|- contracts/       Pydantic v2 event/message models, JSON Schema export
|- domain/          entities, value objects, policies — no IO
|- application/      use cases, ports (Protocols), the pipeline
|- infrastructure/   adapters — db, vectors, blob, bus, health, media, ml
\- entrypoints/      http/ (FastAPI), workers/ (ingest, enroll), cli.py
docker/              Dockerfile (builder -> test -> runtime), docker-compose.yml
```

