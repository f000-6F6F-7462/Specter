"""Socket.IO connector from the edge agent to the remote cloud backend."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import socketio  # type: ignore[import-untyped]

from specter.config.settings import Settings

logger = logging.getLogger(__name__)


class CloudConnector:
    """Manages the outbound Socket.IO connection and messaging with fa-server."""

    def __init__(
        self,
        settings: Settings,
        on_cameras_sync: Callable[[list[dict[str, Any]]], Awaitable[None]] | None = None,
        on_cameras_updated: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        on_connected: Callable[[], Awaitable[None]] | None = None,
        on_camera_start: Callable[[str], Awaitable[None]] | None = None,
        on_camera_stop: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        """Initializes the cloud connector with settings and event callbacks."""
        self._settings = settings
        self._on_cameras_sync = on_cameras_sync
        self._on_cameras_updated = on_cameras_updated
        self._on_connected = on_connected
        self._on_camera_start = on_camera_start
        self._on_camera_stop = on_camera_stop
        self._sio = socketio.AsyncClient(
            reconnection=True,
            reconnection_attempts=0,  # retry indefinitely
            reconnection_delay=2.0,
            reconnection_delay_max=30.0,
        )
        self._is_connected = False
        self._register_handlers()

    @property
    def is_connected(self) -> bool:
        """Returns True if the socket is currently connected to the backend."""
        return self._is_connected

    @property
    def sio(self) -> Any:
        """Returns the underlying Socket.IO AsyncClient."""
        return self._sio

    def _register_handlers(self) -> None:
        """Registers Socket.IO event handlers for the /agent namespace."""

        async def on_connect() -> None:
            self._is_connected = True
            logger.info(
                "Connected to cloud backend at %s (/agent namespace)",
                self._settings.agent.backend_url,
            )
            try:
                await self._sio.emit(
                    "agent:heartbeat",
                    {"name": self._settings.agent.agent_name, "version": "0.2.0"},
                    namespace="/agent",
                )
            except Exception as error:
                logger.debug("Failed to send initial heartbeat: %s", error)

            if self._on_connected is not None:
                try:
                    await self._on_connected()
                except Exception as error:
                    logger.error("Error in on_connected callback: %s", error)

        async def on_disconnect() -> None:
            self._is_connected = False
            logger.warning("Disconnected from cloud backend")

        async def on_connect_error(data: Any) -> None:
            self._is_connected = False
            logger.warning("Connection error to cloud backend: %s", data)

        async def on_cameras_sync_event(data: dict[str, Any]) -> None:
            cameras = data.get("cameras", [])
            logger.info("Received cameras:sync event with %d cameras", len(cameras))
            if self._on_cameras_sync is not None:
                await self._on_cameras_sync(cameras)

        async def on_cameras_updated_event(data: dict[str, Any]) -> None:
            logger.info("Received cameras:updated event: %s", data)
            if self._on_cameras_updated is not None:
                await self._on_cameras_updated(data)

        async def on_camera_start_event(data: dict[str, Any]) -> None:
            camera_id = data.get("cameraId", "")
            logger.info("Cloud requested camera start: %s", camera_id)
            if camera_id and self._on_camera_start is not None:
                await self._on_camera_start(camera_id)

        async def on_camera_stop_event(data: dict[str, Any]) -> None:
            camera_id = data.get("cameraId", "")
            logger.info("Cloud requested camera stop: %s", camera_id)
            if camera_id and self._on_camera_stop is not None:
                await self._on_camera_stop(camera_id)

        async def on_heartbeat_ack(data: dict[str, Any]) -> None:
            logger.debug("Received heartbeat ack: %s", data)

        self._sio.on("connect", on_connect, namespace="/agent")
        self._sio.on("disconnect", on_disconnect, namespace="/agent")
        self._sio.on("connect_error", on_connect_error, namespace="/agent")
        self._sio.on("cameras:sync", on_cameras_sync_event, namespace="/agent")
        self._sio.on("cameras:updated", on_cameras_updated_event, namespace="/agent")
        self._sio.on("agent:camera:start", on_camera_start_event, namespace="/agent")
        self._sio.on("agent:camera:stop", on_camera_stop_event, namespace="/agent")
        self._sio.on("agent:heartbeat:ack", on_heartbeat_ack, namespace="/agent")

    async def connect(self) -> None:
        """Connects to the cloud backend at /agent with authentication token."""
        auth = {"token": self._settings.agent.agent_token}
        url = self._settings.agent.backend_url.rstrip("/")
        logger.info("Connecting to cloud backend at %s...", url)
        try:
            await self._sio.connect(
                url,
                namespaces=["/agent"],
                auth=auth,
                transports=["websocket", "polling"],
            )
        except Exception as error:
            logger.warning(
                "Initial connection to %s failed (%s); will retry in background", url, error
            )

    async def push_cameras_sync(self, cameras: list[dict[str, Any]]) -> None:
        """Pushes the local camera catalog to the cloud backend."""
        if self._sio.connected:
            await self._sio.emit(
                "agent:cameras:sync",
                {"cameras": cameras},
                namespace="/agent",
            )
            logger.info("Pushed %d local cameras to cloud backend", len(cameras))

    async def request_camera_sync(self) -> None:
        """Emits a request to the backend for an immediate camera sync."""
        if self._sio.connected:
            await self._sio.emit("cameras:request_sync", namespace="/agent")

    async def run_heartbeat_loop(self, shutdown_requested: asyncio.Event) -> None:
        """Periodically emits heartbeats until shutdown is requested."""
        interval = self._settings.agent.heartbeat_interval_seconds
        while not shutdown_requested.is_set():
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break

            if shutdown_requested.is_set():
                break

            if self._sio.connected:
                try:
                    payload = {
                        "name": self._settings.agent.agent_name,
                        "version": "0.2.0",
                    }
                    await self._sio.emit("agent:heartbeat", payload, namespace="/agent")
                    logger.debug("Sent heartbeat to cloud backend")
                except Exception as error:
                    logger.debug("Failed to send heartbeat: %s", error)

    async def disconnect(self) -> None:
        """Gracefully disconnects from the backend."""
        if self._sio.connected:
            try:
                await self._sio.disconnect()
            except Exception as error:
                logger.debug("Error during disconnect: %s", error)
        self._is_connected = False
