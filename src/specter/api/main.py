"""Builds and serves the FastAPI application."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import partial

import uvicorn
from fastapi import FastAPI

from specter import __version__
from specter.api.routers import health
from specter.config.settings import Settings
from specter.messaging.client import MessageBus


@asynccontextmanager
async def lifespan(app: FastAPI, *, settings: Settings) -> AsyncGenerator[None]:
    """Keeps a NATS connection open for as long as the application runs."""
    message_bus = await MessageBus.connect(settings.services.nats_url, client_name="specter-api")
    try:
        await message_bus.declare_streams()
        app.state.message_bus = message_bus
        yield
    finally:
        await message_bus.close()


def create_app(settings: Settings) -> FastAPI:
    """Returns the application, which connects to NATS while it is running."""
    app = FastAPI(
        title="Specter", version=__version__, lifespan=partial(lifespan, settings=settings)
    )
    app.include_router(health.router)
    return app


def run(settings: Settings) -> None:
    """Serves the API until the process is stopped."""
    # Logging is already configured by the command line; uvicorn must not replace it.
    uvicorn.run(
        create_app(settings), host=settings.api.host, port=settings.api.port, log_config=None
    )
