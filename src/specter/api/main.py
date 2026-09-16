"""Builds and serves the FastAPI application."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import partial

import uvicorn
from fastapi import FastAPI

from specter import __version__
from specter.api.routers import health
from specter.config.settings import Settings
from specter.messaging.client import MessageBus

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI, *, settings: Settings) -> AsyncGenerator[None]:
    """Serves requests at once and connects to NATS in the background until it is reachable.

    Until the connection opens, the health check reports that NATS is not connected.
    """
    message_bus = MessageBus(client_name="specter-api")
    app.state.message_bus = message_bus
    connection_task = asyncio.create_task(prepare_message_bus(message_bus, settings))
    try:
        yield
    finally:
        connection_task.cancel()
        await asyncio.gather(connection_task, return_exceptions=True)
        await message_bus.close()


async def prepare_message_bus(message_bus: MessageBus, settings: Settings) -> None:
    """Connects to NATS and declares the streams, logging a failure instead of stopping the API."""
    try:
        await message_bus.connect(settings.services.nats_url)
        await message_bus.declare_streams(settings.matching.cooldown_seconds)
    except Exception:
        logger.exception("cannot prepare the NATS streams")


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
