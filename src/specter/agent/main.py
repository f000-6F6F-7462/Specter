"""Edge agent entrypoint: coordinates cloud connector and camera synchronizer."""

import asyncio
import logging
from typing import Any

from specter.agent.alert_forwarder import AlertForwarder
from specter.agent.camera_sync import CameraSyncReconciler
from specter.agent.cloud_connector import CloudConnector
from specter.agent.offline_queue import OfflineQueue
from specter.config.settings import Settings
from specter.messaging.message_bus import MessageBus

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


def _build_cloud_connector(
    settings: Settings,
    reconciler: CameraSyncReconciler,
) -> CloudConnector:
    """Builds and wires the CloudConnector with reconciliation and control callbacks."""
    connector: CloudConnector | None = None

    async def on_cameras_sync(cameras: list[dict[str, Any]]) -> None:
        logger.info("Reconciling %d cameras with local Specter engine", len(cameras))
        await reconciler.reconcile(cameras)

    async def on_cameras_updated(data: dict[str, Any]) -> None:
        logger.info("Camera change detected in cloud: %s; requesting sync", data)
        if connector is not None:
            await connector.request_camera_sync()

    async def on_connected() -> None:
        try:
            local_cameras = await reconciler.fetch_local_cameras()
            if connector is not None:
                await connector.push_cameras_sync(local_cameras)
                await connector.request_camera_sync()
        except Exception as error:
            logger.warning("Failed to push initial camera sync to cloud: %s", error)

    async def on_camera_start(camera_id: str) -> None:
        try:
            await reconciler.start_camera(camera_id)
        except Exception as error:
            logger.error("Failed to start camera %s: %s", camera_id, error)

    async def on_camera_stop(camera_id: str) -> None:
        try:
            await reconciler.stop_camera(camera_id)
        except Exception as error:
            logger.error("Failed to stop camera %s: %s", camera_id, error)

    connector = CloudConnector(
        settings=settings,
        on_cameras_sync=on_cameras_sync,
        on_cameras_updated=on_cameras_updated,
        on_connected=on_connected,
        on_camera_start=on_camera_start,
        on_camera_stop=on_camera_stop,
    )
    return connector


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

    queue_db_path = settings.paths.data_directory / "agent_offline_queue.sqlite3"
    offline_queue = OfflineQueue(queue_db_path)

    message_bus = MessageBus(client_name="specter-agent")
    try:
        await message_bus.connect(settings.services.nats_url)
        logger.info("Agent connected to local NATS at %s", settings.services.nats_url)
    except Exception as error:
        logger.warning("Agent could not connect to NATS immediately: %s", error)

    connector = _build_cloud_connector(settings, reconciler)

    alert_forwarder = AlertForwarder(
        owner_id=settings.agent.owner_id,
        message_bus=message_bus,
        cloud_connector=connector,
        offline_queue=offline_queue,
        evidence_root=settings.paths.data_directory,
    )

    # Connect to the cloud backend
    await connector.connect()

    # Launch background tasks
    heartbeat_task = asyncio.create_task(connector.run_heartbeat_loop(shutdown_requested))
    forwarder_task = asyncio.create_task(alert_forwarder.run(shutdown_requested))

    try:
        await shutdown_requested.wait()
    finally:
        logger.info("Shutting down Specter edge agent...")
        heartbeat_task.cancel()
        forwarder_task.cancel()
        await asyncio.gather(heartbeat_task, forwarder_task, return_exceptions=True)
        await connector.disconnect()
        await message_bus.close()
        logger.info("Specter edge agent stopped cleanly.")
