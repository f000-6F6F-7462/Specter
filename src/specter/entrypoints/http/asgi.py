"""FastAPI application factory.

Tables are managed by Alembic.
"""

import contextlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI

from specter import __version__
from specter.core.di import Container, build_container
from specter.entrypoints.http.deps import require_api_key
from specter.entrypoints.http.errors import install_error_handlers
from specter.entrypoints.http.routers import alerts, streams, targets, watchlists


def create_app(container: Container | None = None) -> FastAPI:
    container = container or build_container()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        async with contextlib.AsyncExitStack() as stack:
            for adapter in container.lifecycle:
                await stack.enter_async_context(adapter)
            yield
        await container.engine.dispose()

    app = FastAPI(
        title="Specter Vision Engine",
        version=__version__,
        summary="Headless control-plane API for target and alert management.",
        lifespan=lifespan,
    )
    app.state.container = container
    install_error_handlers(app)

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    api = APIRouter(prefix="/api", dependencies=[Depends(require_api_key)])
    api.include_router(watchlists.router)
    api.include_router(targets.router)
    api.include_router(streams.router)
    api.include_router(alerts.router)
    app.include_router(api)
    return app


def main() -> None:
    import uvicorn  # pylint: disable=import-outside-toplevel

    uvicorn.run(
        "specter.entrypoints.http.asgi:create_app",
        factory=True,
        host="0.0.0.0",
        port=8000,
    )
