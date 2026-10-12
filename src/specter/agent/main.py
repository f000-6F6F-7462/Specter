"""Edge agent entrypoint: coordinates cloud connector and camera synchronizer."""

import asyncio
import logging
from typing import Any

from specter.agent.camera_sync import CameraSyncReconciler
from specter.agent.cloud_connector import CloudConnector
from specter.config.settings import Settings

logger = logging.getLogger(__name__)


def _resolve_api_token(settings: Settings) -> str:
    """Resolves the local Specter API bearer token from settings or token file."""
    if settings.agent.specter_api_token:
        return settings.agent.specter_api_token
    token_file = settings.security.api_token_file
    if token_file.is_file():
        try:
            return token_file.read_text(encoding="utf-8").strip()
        except OSError as error:
            logger.warning("Could not read API token file %s: %s", token_file, error)
    return ""


async def run(settings: Settings, shutdown_requested: asyncio.Event) -> None:
    """Runs the Specter edge agent process until shutdown is requested."""
    logger.info(
        "Starting Specter edge agent (mode: %s, target: %s)",
        settings.agent.mode,
        settings.agent.backend_url,
    )

    api_token = _resolve_api_token(settings)
    reconciler = CameraSyncReconciler(
        specter_api_url=settings.agent.specter_api_url,
        owner_id=settings.agent.owner_id,
        api_token=api_token,
    )

    connector: CloudConnector | None = None

    async def on_cameras_sync(cameras: list[dict[str, Any]]) -> None:
        """Handles incoming cameras sync payload from backend."""
        logger.info("Reconciling %d cameras with local Specter engine", len(cameras))
        await reconciler.reconcile(cameras)

    async def on_cameras_updated(data: dict[str, Any]) -> None:
        """Handles incremental camera update notification by requesting fresh sync."""
        logger.info("Camera change detected in cloud: %s; requesting sync", data)
        if connector is not None:
            await connector.request_camera_sync()

    connector = CloudConnector(
        settings=settings,
        on_cameras_sync=on_cameras_sync,
        on_cameras_updated=on_cameras_updated,
    )

    # Connect to the cloud backend
    await connector.connect()

    # Launch background heartbeat loop
    heartbeat_task = asyncio.create_task(connector.run_heartbeat_loop(shutdown_requested))

    try:
        await shutdown_requested.wait()
    finally:
        logger.info("Shutting down Specter edge agent...")
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
        await connector.disconnect()
        logger.info("Specter edge agent stopped cleanly.")
