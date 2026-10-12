"""Edge agent entrypoint: coordinates cloud connector and camera synchronizer."""

import asyncio
import json
import logging
from typing import Any

from specter.agent.alert_forwarder import AlertForwarder
from specter.agent.camera_sync import CameraSyncReconciler
from specter.agent.cloud_connector import CloudConnector
from specter.agent.offline_queue import OfflineQueue
from specter.agent.watchlist_sync import WatchlistSyncReconciler
from specter.agent.webrtc_relay import WebRtcRelay
from specter.config.settings import Settings
from specter.messaging.message_bus import MessageBus
from specter.messaging.subjects import CameraEvent, build_all_cameras_subject

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


def _register_camera_handlers(connector: CloudConnector, reconciler: CameraSyncReconciler) -> None:
    """Registers handlers for camera start and stop commands received from cloud."""

    async def on_camera_start(data: dict[str, Any]) -> None:
        camera_id = data.get("cameraId", "")
        logger.info("Received cloud command to start camera: %s", camera_id)
        success = await reconciler.start_local_camera(camera_id)
        if success:
            await connector.sio.emit(
                "agent:camera:status_changed",
                {"cameraId": camera_id, "status": "starting"},
                namespace="/agent",
            )

    async def on_camera_stop(data: dict[str, Any]) -> None:
        camera_id = data.get("cameraId", "")
        logger.info("Received cloud command to stop camera: %s", camera_id)
        success = await reconciler.stop_local_camera(camera_id)
        if success:
            await connector.sio.emit(
                "agent:camera:status_changed",
                {"cameraId": camera_id, "status": "stopped"},
                namespace="/agent",
            )

    connector.sio.on("agent:camera:start", on_camera_start, namespace="/agent")
    connector.sio.on("agent:camera:stop", on_camera_stop, namespace="/agent")


async def _subscribe_camera_status_forwarder(
    message_bus: MessageBus, connector: CloudConnector
) -> None:
    """Subscribes to camera status change events on local NATS and forwards them to cloud."""
    status_subject = build_all_cameras_subject(CameraEvent.STATUS_CHANGED)

    async def _on_status_message(raw_msg: Any) -> None:
        try:
            data = json.loads(raw_msg.data.decode("utf-8"))
            cam_id = data.get("camera_id") or data.get("entity_id", "")
            new_status = data.get("status") or data.get("live_status", "")
            if cam_id and new_status:
                await connector.sio.emit(
                    "agent:camera:status_changed",
                    {"cameraId": cam_id, "status": new_status},
                    namespace="/agent",
                )
        except Exception as error:
            logger.error("Failed to forward camera status change: %s", error)

    nats_conn = getattr(message_bus, "_connection", None)
    if nats_conn is not None:
        try:
            await nats_conn.subscribe(status_subject, cb=_on_status_message)
            logger.info("Subscribed to camera status changes: %s", status_subject)
        except Exception as error:
            logger.warning("Could not subscribe to camera status changes: %s", error)


async def _connect_message_bus(nats_url: str) -> MessageBus:
    """Creates and connects the local message bus instance."""
    message_bus = MessageBus(client_name="specter-agent")
    try:
        await message_bus.connect(nats_url)
        logger.info("Agent connected to local NATS at %s", nats_url)
    except Exception as error:
        logger.warning("Agent could not connect to NATS immediately: %s", error)
    return message_bus


def _setup_watchlist_sync(
    connector: CloudConnector, settings: Settings, api_token: str
) -> WatchlistSyncReconciler:
    """Configures watchlist synchronizer and registers its Socket.IO event handler."""
    watchlist_reconciler = WatchlistSyncReconciler(
        specter_api_url=settings.agent.specter_api_url,
        owner_id=settings.agent.owner_id,
        local_owner_id=settings.agent.local_owner_id,
        api_token=api_token,
        backend_url=settings.agent.backend_url,
        agent_token=settings.agent.agent_token,
    )

    async def on_watchlists_sync(data: dict[str, Any]) -> None:
        watchlists = data.get("watchlists", [])
        logger.info("Reconciling %d watchlists with local Specter engine", len(watchlists))
        await watchlist_reconciler.reconcile_watchlists(watchlists)

    connector.sio.on("watchlists:sync", on_watchlists_sync, namespace="/agent")
    return watchlist_reconciler


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
        local_owner_id=settings.agent.local_owner_id,
        api_token=api_token,
    )

    queue_db_path = settings.paths.data_directory / "agent_offline_queue.sqlite3"
    offline_queue = OfflineQueue(queue_db_path)
    message_bus = await _connect_message_bus(settings.services.nats_url)

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

    async def on_connected() -> None:
        """Pushes the local camera catalog upon connection to the cloud backend."""
        logger.info("Connected to cloud backend; pushing local cameras catalog")
        if connector is not None:
            await reconciler.push_local_cameras(connector)

    connector = CloudConnector(
        settings=settings,
        on_cameras_sync=on_cameras_sync,
        on_cameras_updated=on_cameras_updated,
        on_connected=on_connected,
    )

    _register_camera_handlers(connector, reconciler)
    await _subscribe_camera_status_forwarder(message_bus, connector)

    alert_forwarder = AlertForwarder(
        owner_id=settings.agent.owner_id,
        message_bus=message_bus,
        cloud_connector=connector,
        offline_queue=offline_queue,
        evidence_root=settings.paths.data_directory,
    )

    webrtc_relay = WebRtcRelay(
        cloud_connector=connector,
        specter_api_url=settings.agent.specter_api_url,
        owner_id=settings.agent.local_owner_id,
        api_token=api_token,
    )
    webrtc_relay.register_handlers()
    _setup_watchlist_sync(connector, settings, api_token)

    await connector.connect(shutdown_requested=shutdown_requested)

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
        await webrtc_relay.close()
        await connector.disconnect()
        await message_bus.close()
        logger.info("Specter edge agent stopped cleanly.")
