"""Edge agent entrypoint: coordinates cloud connector and camera synchronizer."""

import asyncio
import json
import logging
from pathlib import Path
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
    """Configures watchlist synchronizer and registers its Socket.IO event handlers."""
    watchlist_reconciler = WatchlistSyncReconciler(
        specter_api_url=settings.agent.specter_api_url,
        owner_id=settings.agent.owner_id,
        api_token=api_token,
        backend_url=settings.agent.backend_url,
        agent_token=settings.agent.agent_token,
    )

    async def on_watchlists_sync(data: dict[str, Any]) -> None:
        watchlists = data.get("watchlists", [])
        logger.info("Reconciling %d watchlists with local Specter engine", len(watchlists))
        await watchlist_reconciler.reconcile_watchlists(watchlists)

    async def on_watchlists_request_sync() -> None:
        logger.info("Received agent:watchlists:request_sync from cloud")
        await watchlist_reconciler.push_local_watchlists(connector)

    async def on_watchlist_create(data: dict[str, Any]) -> None:
        wl = data.get("watchlist", {})
        if wl:
            logger.info("Received agent:watchlist:create from cloud: %s", wl.get("name"))
            await watchlist_reconciler.create_local_watchlist(wl)

    async def on_watchlist_update(data: dict[str, Any]) -> None:
        wl_id = data.get("watchlistId", "")
        changes = data.get("changes", {})
        if wl_id and changes:
            logger.info("Received agent:watchlist:update from cloud: %s", wl_id)
            await watchlist_reconciler.update_local_watchlist(wl_id, changes)

    async def on_watchlist_delete(data: dict[str, Any]) -> None:
        wl_id = data.get("watchlistId", "")
        if wl_id:
            logger.info("Received agent:watchlist:delete from cloud: %s", wl_id)
            await watchlist_reconciler.delete_local_watchlist(wl_id)

    connector.sio.on("watchlists:sync", on_watchlists_sync, namespace="/agent")
    connector.sio.on(
        "agent:watchlists:request_sync", on_watchlists_request_sync, namespace="/agent"
    )
    connector.sio.on("agent:watchlist:create", on_watchlist_create, namespace="/agent")
    connector.sio.on("agent:watchlist:update", on_watchlist_update, namespace="/agent")
    connector.sio.on("agent:watchlist:delete", on_watchlist_delete, namespace="/agent")
    return watchlist_reconciler


def _build_cloud_connector(
    settings: Settings,
    reconciler: CameraSyncReconciler,
    watchlist_reconciler_holder: list[WatchlistSyncReconciler],
    alert_forwarder_holder: list[AlertForwarder],
) -> CloudConnector:
    """Builds CloudConnector instance with registered event hooks."""
    connector_holder: list[CloudConnector] = []

    async def on_cameras_sync(cameras: list[dict[str, Any]]) -> None:
        logger.info("Reconciling %d cameras with local Specter engine", len(cameras))
        if not cameras:
            logger.info("Ignoring empty camera sync payload to preserve local cameras")
            return
        await reconciler.reconcile(cameras)

    async def on_cameras_updated(data: dict[str, Any]) -> None:
        logger.info("Camera change detected in cloud: %s; requesting sync", data)
        if connector_holder:
            await connector_holder[0].request_camera_sync()

    async def on_connected() -> None:
        logger.info("Connected to cloud backend; pushing local catalog and backfilling alerts")
        if connector_holder:
            try:
                await reconciler.push_local_cameras(connector_holder[0])
                if watchlist_reconciler_holder:
                    await watchlist_reconciler_holder[0].push_local_watchlists(connector_holder[0])
                if alert_forwarder_holder:
                    await alert_forwarder_holder[0].backfill_recent_alerts(limit=100)
                await connector_holder[0].request_camera_sync()
            except Exception as error:
                logger.warning("Failed to push catalog or alerts on connect: %s", error)

    async def on_camera_start(camera_id: str) -> None:
        try:
            success = await reconciler.start_local_camera(camera_id)
            if success and connector_holder:
                await connector_holder[0].sio.emit(
                    "agent:camera:status_changed",
                    {"cameraId": camera_id, "status": "starting"},
                    namespace="/agent",
                )
        except Exception as error:
            logger.error("Failed to start camera %s: %s", camera_id, error)

    async def on_camera_stop(camera_id: str) -> None:
        try:
            success = await reconciler.stop_local_camera(camera_id)
            if success and connector_holder:
                await connector_holder[0].sio.emit(
                    "agent:camera:status_changed",
                    {"cameraId": camera_id, "status": "stopped"},
                    namespace="/agent",
                )
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
    connector_holder.append(connector)
    return connector


async def _run_periodic_camera_sync(
    connector: CloudConnector,
    reconciler: CameraSyncReconciler,
    shutdown_ev: asyncio.Event,
) -> None:
    """Periodically pushes local camera catalog to keep cloud state synchronized."""
    while not shutdown_ev.is_set():
        try:
            for _ in range(30):
                if shutdown_ev.is_set():
                    return
                await asyncio.sleep(1)
            if connector.sio.connected:
                logger.debug("Pushing local camera catalog (periodic sync)")
                await reconciler.push_local_cameras(connector)
        except asyncio.CancelledError:
            break
        except Exception as err:
            logger.warning("Periodic camera sync error: %s", err)


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

    queue_dir = settings.paths.data_directory
    try:
        queue_dir.mkdir(parents=True, exist_ok=True)
    except (PermissionError, OSError):
        queue_dir = Path.home() / ".specter"
        queue_dir.mkdir(parents=True, exist_ok=True)
    queue_db_path = queue_dir / "agent_offline_queue.sqlite3"
    offline_queue = OfflineQueue(queue_db_path)
    message_bus = await _connect_message_bus(settings.services.nats_url)

    watchlist_reconciler_holder: list[WatchlistSyncReconciler] = []
    alert_forwarder_holder: list[AlertForwarder] = []

    connector = _build_cloud_connector(
        settings,
        reconciler,
        watchlist_reconciler_holder,
        alert_forwarder_holder,
    )
    await _subscribe_camera_status_forwarder(message_bus, connector)

    alert_forwarder = AlertForwarder(
        owner_id=settings.agent.owner_id,
        message_bus=message_bus,
        cloud_connector=connector,
        offline_queue=offline_queue,
        evidence_root=settings.paths.data_directory,
        specter_api_url=settings.agent.specter_api_url,
        api_token=api_token,
    )
    alert_forwarder_holder.append(alert_forwarder)

    async def on_alerts_request_sync() -> None:
        logger.info("Received agent:alerts:request_sync from cloud")
        await alert_forwarder.backfill_recent_alerts(limit=100)

    connector.sio.on("agent:alerts:request_sync", on_alerts_request_sync, namespace="/agent")

    webrtc_relay = WebRtcRelay(
        cloud_connector=connector,
        specter_api_url=settings.agent.specter_api_url,
        owner_id=settings.agent.local_owner_id,
        api_token=api_token,
    )
    webrtc_relay.register_handlers()
    watchlist_reconciler = _setup_watchlist_sync(connector, settings, api_token)
    watchlist_reconciler_holder.append(watchlist_reconciler)

    await connector.connect()

    # Launch background tasks
    heartbeat_task = asyncio.create_task(connector.run_heartbeat_loop(shutdown_requested))
    forwarder_task = asyncio.create_task(alert_forwarder.run(shutdown_requested))
    sync_task = asyncio.create_task(
        _run_periodic_camera_sync(connector, reconciler, shutdown_requested)
    )

    try:
        await shutdown_requested.wait()
    finally:
        logger.info("Shutting down Specter edge agent...")
        heartbeat_task.cancel()
        forwarder_task.cancel()
        sync_task.cancel()
        await asyncio.gather(heartbeat_task, forwarder_task, sync_task, return_exceptions=True)
        await webrtc_relay.close()
        await connector.disconnect()
        await message_bus.close()
        logger.info("Specter edge agent stopped cleanly.")
