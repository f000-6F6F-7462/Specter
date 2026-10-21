"""Listens for local Specter alerts over NATS and forwards them or buffers offline."""

import asyncio
import base64
import json
import logging
from pathlib import Path
from typing import Any

import httpx

from specter.agent.cloud_connector import CloudConnector
from specter.agent.offline_queue import OfflineQueue
from specter.messaging.message_bus import MessageBus
from specter.messaging.subjects import SUBJECT_WILDCARD, CameraEvent, build_camera_subject

logger = logging.getLogger(__name__)


class AlertForwarder:
    """Consumes alerts from NATS and forwards them to the cloud backend with store-and-forward."""

    def __init__(
        self,
        owner_id: str,
        message_bus: MessageBus,
        cloud_connector: CloudConnector,
        offline_queue: OfflineQueue,
        evidence_root: Path | None = None,
        specter_api_url: str = "",
        api_token: str = "",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initializes the alert forwarder with messaging, connector and offline queue."""
        self._owner_id = owner_id
        self._message_bus = message_bus
        self._connector = cloud_connector
        self._offline_queue = offline_queue
        self._evidence_root = evidence_root
        self._specter_api_url = specter_api_url.rstrip("/")
        self._api_token = api_token
        self._http_client = http_client
        self._background_tasks: set[asyncio.Task[Any]] = set()

    def _read_snapshot_base64(self, snapshot_path: str | None) -> str | None:
        """Reads local snapshot JPEG and encodes it as base64 for transmission."""
        if not snapshot_path:
            return None
        file_path = Path(snapshot_path)
        if not file_path.is_absolute() and self._evidence_root is not None:
            file_path = self._evidence_root / file_path
        if file_path.is_file():
            try:
                data = file_path.read_bytes()
                return base64.b64encode(data).decode("ascii")
            except OSError as error:
                logger.warning("Could not read snapshot %s: %s", file_path, error)
        return None

    async def handle_alert(
        self,
        alert_id: str,
        event_kind: str,
        payload: dict[str, Any],
        snapshot_path: str | None = None,
    ) -> bool:
        """Processes an incoming alert: forwards immediately if online, else queues offline.

        Returns:
            True if sent directly, False if queued locally.
        """
        snapshot_b64 = self._read_snapshot_base64(snapshot_path)
        packet = {
            "alert_id": alert_id,
            "kind": event_kind,
            "payload": payload,
            "snapshot_base64": snapshot_b64,
        }

        if self._connector.is_connected:
            try:
                if self._connector.sio.connected:
                    await self._connector.sio.emit("agent:alert", packet, namespace="/agent")
                    logger.info("Forwarded alert %s (%s) live to cloud", alert_id, event_kind)
                    # Trigger drain of any older buffered alerts
                    drain_task = asyncio.create_task(self.drain_queue())
                    self._background_tasks.add(drain_task)
                    drain_task.add_done_callback(self._background_tasks.discard)
                    return True
            except Exception as error:
                logger.warning("Live alert forward failed (%s); falling back to queue", error)

        # Buffer offline
        self._offline_queue.enqueue(alert_id, event_kind, payload, snapshot_path)
        return False

    async def drain_queue(self, batch_size: int = 25) -> int:
        """Uploads buffered offline alerts to the cloud backend while connected.

        Returns:
            Total number of successfully flushed alerts.
        """
        if not self._connector.is_connected or not self._connector.sio.connected:
            return 0

        total_drained = 0
        while True:
            items = self._offline_queue.peek(limit=batch_size)
            if not items:
                break

            acknowledged_ids: list[str] = []
            for item in items:
                snapshot_b64 = self._read_snapshot_base64(item.snapshot_path)
                packet = {
                    "alert_id": item.alert_id,
                    "kind": item.event_kind,
                    "payload": item.payload,
                    "snapshot_base64": snapshot_b64,
                    "buffered": True,
                    "created_at": item.created_at,
                }
                try:
                    await self._connector.sio.emit("agent:alert", packet, namespace="/agent")
                    acknowledged_ids.append(item.alert_id)
                except Exception as error:
                    logger.warning("Failed to upload buffered alert %s: %s", item.alert_id, error)
                    self._offline_queue.increment_retry([item.alert_id])
                    break

            if acknowledged_ids:
                self._offline_queue.acknowledge(acknowledged_ids)
                total_drained += len(acknowledged_ids)

            # If fewer items than batch_size or error occurred, stop loop
            if len(acknowledged_ids) < len(items):
                break

        if total_drained > 0:
            logger.info("Flushed %d buffered alerts to cloud backend", total_drained)
        return total_drained

    async def run(self, shutdown_requested: asyncio.Event) -> None:
        """Subscribes to NATS alert events and runs the queue drain loop until shutdown."""
        # 1. Subscribe to match_confirmed and rule_triggered events
        match_subject = build_camera_subject(
            self._owner_id, SUBJECT_WILDCARD, CameraEvent.MATCH_CONFIRMED
        )
        rule_subject = build_camera_subject(
            self._owner_id, SUBJECT_WILDCARD, CameraEvent.RULE_TRIGGERED
        )

        async def _on_match_message(raw_msg: Any) -> None:
            try:
                data = json.loads(raw_msg.data.decode("utf-8"))
                alert_id = data.get("message_id", "")
                snapshot_path = data.get("snapshot_path")
                await self.handle_alert(alert_id, "match_confirmed", data, snapshot_path)
            except Exception as error:
                logger.error("Failed to process match_confirmed message: %s", error)

        async def _on_rule_message(raw_msg: Any) -> None:
            try:
                data = json.loads(raw_msg.data.decode("utf-8"))
                alert_id = data.get("message_id", "")
                snapshot_path = data.get("snapshot_path")
                await self.handle_alert(alert_id, "rule_triggered", data, snapshot_path)
            except Exception as error:
                logger.error("Failed to process rule_triggered message: %s", error)

        connection = getattr(self._message_bus, "_connection", None)
        if connection is not None:
            await connection.subscribe(match_subject, cb=_on_match_message)
            await connection.subscribe(rule_subject, cb=_on_rule_message)
            logger.info("Subscribed to alert subjects: %s, %s", match_subject, rule_subject)

        # 2. Periodic drain loop while running
        while not shutdown_requested.is_set():
            try:
                await asyncio.sleep(10.0)
            except asyncio.CancelledError:
                break
            if shutdown_requested.is_set():
                break
            if self._connector.is_connected:
                await self.drain_queue()

    async def fetch_alert_snapshot(self, alert_id: str) -> tuple[str, bytes] | None:
        """Fetches an alert snapshot JPEG from local filesystem or Specter API."""
        if self._evidence_root is not None:
            snapshot_path = self._evidence_root / f"evidence/{alert_id}.jpg"
            if snapshot_path.is_file():
                try:
                    return "image/jpeg", snapshot_path.read_bytes()
                except OSError:
                    pass

        if self._specter_api_url:
            url = f"{self._specter_api_url}/owners/{self._owner_id}/alerts/{alert_id}/snapshot"
            headers: dict[str, str] = {}
            if self._api_token:
                headers["Authorization"] = f"Bearer {self._api_token}"
            client = self._http_client or httpx.AsyncClient(timeout=10.0)
            should_close = self._http_client is None
            try:
                response = await client.get(url, headers=headers)
                if response.status_code == httpx.codes.OK:
                    content_type = response.headers.get("content-type", "image/jpeg")
                    return content_type, response.content
            except Exception as error:
                logger.warning(
                    "Failed to fetch alert snapshot %s from local API: %s", alert_id, error
                )
            finally:
                if should_close:
                    await client.aclose()
        return None

    async def backfill_recent_alerts(self, limit: int = 100) -> int:
        """Fetches recent historical alerts from local Specter engine and syncs them to the cloud.

        Args:
            limit: Maximum number of recent alerts to retrieve.

        Returns:
            Number of alerts pushed to cloud.
        """
        if not self._specter_api_url or not self._connector.is_connected:
            return 0

        url = f"{self._specter_api_url}/owners/{self._owner_id}/alerts"
        headers = {"Content-Type": "application/json"}
        if self._api_token:
            headers["Authorization"] = f"Bearer {self._api_token}"

        should_close = self._http_client is None
        client = self._http_client or httpx.AsyncClient(timeout=15.0)
        try:
            response = await client.get(url, params={"limit": limit}, headers=headers)
            if response.status_code != httpx.codes.OK:
                logger.warning(
                    "Could not fetch local alerts for backfill: status %d", response.status_code
                )
                return 0
            data = response.json()
            alerts = data.get("alerts", [])
            packets = []
            for alert in alerts:
                snapshot_b64 = None
                if alert.get("has_snapshot"):
                    alert_id = alert.get("id")
                    if alert_id:
                        snap_data = await self.fetch_alert_snapshot(alert_id)
                        if snap_data:
                            snapshot_b64 = base64.b64encode(snap_data[1]).decode("ascii")
                packets.append(
                    {
                        "alert_id": alert.get("id"),
                        "kind": alert.get("kind"),
                        "payload": alert,
                        "snapshot_base64": snapshot_b64,
                    }
                )

            if packets and self._connector.sio.connected:
                await self._connector.sio.emit(
                    "agent:alerts:sync",
                    {"alerts": packets},
                    namespace="/agent",
                )
                logger.info("Backfilled %d historical alerts to cloud", len(packets))
                return len(packets)
            return 0
        except Exception as error:
            logger.warning("Failed to backfill historical alerts to cloud: %s", error)
            return 0
        finally:
            if should_close:
                await client.aclose()
