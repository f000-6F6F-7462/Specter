"""Builds and serves the FastAPI application."""

import asyncio
import logging
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from functools import partial

import httpx
import uvicorn
from fastapi import Depends, FastAPI

from specter import __version__
from specter.api.authentication import generate_api_token, require_api_token
from specter.api.dependencies import ApiServices
from specter.api.errors import register_error_handlers
from specter.api.routers import (
    alerts,
    cameras,
    health,
    live_video,
    owners,
    rules,
    targets,
    watchlists,
    zones,
)
from specter.config.settings import Settings
from specter.messaging.client import MessageBus
from specter.storage.credentials import (
    CredentialCipher,
    load_or_create_credentials_key,
    load_or_create_secret,
)
from specter.storage.database import DatabaseThread, open_database
from specter.storage.evidence import EvidenceStore
from specter.storage.reference_images import ReferenceImageStore
from specter.storage.vector_index import VectorIndex

logger = logging.getLogger(__name__)

# Live video streams run for as long as someone watches, so only connecting has a timeout.
GO2RTC_CONNECT_TIMEOUT_SECONDS = 5.0


@asynccontextmanager
async def lifespan(app: FastAPI, *, settings: Settings) -> AsyncGenerator[None]:
    """Opens the API's services and serves requests at once, connecting to NATS in the background.

    Until the connection opens, the health check reports that NATS is not connected.
    """
    message_bus = MessageBus(client_name="specter-api")
    database_thread = DatabaseThread(open_database(settings.paths.database_file))
    vector_index = VectorIndex.connect(settings.services.qdrant_url)
    go2rtc_http_client = httpx.AsyncClient(
        base_url=settings.services.go2rtc_url,
        timeout=httpx.Timeout(None, connect=GO2RTC_CONNECT_TIMEOUT_SECONDS),
    )
    app.state.services = ApiServices(
        settings=settings,
        api_token=load_or_create_secret(settings.security.api_token_file, generate_api_token),
        message_bus=message_bus,
        database_thread=database_thread,
        cipher=CredentialCipher(
            load_or_create_credentials_key(settings.security.credentials_key_file)
        ),
        vector_index=vector_index,
        evidence_store=EvidenceStore(settings.paths.data_directory),
        reference_image_store=ReferenceImageStore(settings.paths.data_directory),
        go2rtc_http_client=go2rtc_http_client,
    )
    connection_task = asyncio.create_task(prepare_message_bus(message_bus, settings))
    try:
        yield
    finally:
        connection_task.cancel()
        await asyncio.gather(connection_task, return_exceptions=True)
        await message_bus.close()
        await go2rtc_http_client.aclose()
        await vector_index.close()
        database_thread.close()


async def prepare_message_bus(message_bus: MessageBus, settings: Settings) -> None:
    """Connects to NATS and declares the streams, logging a failure instead of stopping the API."""
    try:
        await message_bus.connect(settings.services.nats_url)
        await message_bus.declare_streams(settings.matching.cooldown_seconds)
    except Exception:
        logger.exception("cannot prepare the NATS streams")


def create_app(settings: Settings) -> FastAPI:
    """Returns the application; every route except the health check needs the API token."""
    app = FastAPI(
        title="Specter", version=__version__, lifespan=partial(lifespan, settings=settings)
    )
    register_error_handlers(app)
    app.include_router(health.router)
    for router in (
        owners.router,
        cameras.router,
        zones.router,
        rules.router,
        watchlists.router,
        targets.router,
        alerts.router,
        live_video.router,
    ):
        app.include_router(router, dependencies=[Depends(require_api_token)])
    app.include_router(live_video.websocket_router)
    return app


class SignalFreeServer(uvicorn.Server):
    """A uvicorn server that leaves stop signals to Specter's own shutdown handling.

    uvicorn re-raises a caught SIGTERM after it stops, which would end the process with 143
    instead of a clean exit.
    """

    @contextmanager
    def capture_signals(self) -> Generator[None]:
        """Captures nothing, because the shutdown event already reacts to stop signals."""
        yield


async def serve(settings: Settings, shutdown_requested: asyncio.Event) -> None:
    """Serves the API until shutdown is requested, then finishes the requests in progress."""
    # Logging is already configured by the command line; uvicorn must not replace it.
    server = SignalFreeServer(
        uvicorn.Config(
            create_app(settings), host=settings.api.host, port=settings.api.port, log_config=None
        )
    )
    serving_task = asyncio.create_task(server.serve())
    shutdown_task = asyncio.create_task(shutdown_requested.wait())
    try:
        await asyncio.wait((serving_task, shutdown_task), return_when=asyncio.FIRST_COMPLETED)
    finally:
        shutdown_task.cancel()
        server.should_exit = True
        await serving_task
