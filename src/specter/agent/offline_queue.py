"""Persistent SQLite offline alert queue for store-and-forward edge operation."""

import json
import logging
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OfflineAlertItem:
    """An alert queued locally while the edge agent is disconnected from the cloud."""

    id: int
    alert_id: str
    event_kind: str
    payload: dict[str, Any]
    snapshot_path: str | None
    created_at: str
    retry_count: int


class OfflineQueue:
    """Thread-safe SQLite persistent store for offline alert queuing."""

    def __init__(self, database_path: Path) -> None:
        """Initializes the offline queue database and creates tables if missing."""
        self._database_path = database_path
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def _get_connection(self) -> sqlite3.Connection:
        """Opens a SQLite connection with WAL journal mode."""
        connection = sqlite3.connect(str(self._database_path), timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def _initialize_schema(self) -> None:
        """Creates the offline_alerts table and index."""
        with self._get_connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS offline_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_id TEXT UNIQUE NOT NULL,
                    event_kind TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    snapshot_path TEXT,
                    created_at TEXT NOT NULL,
                    retry_count INTEGER DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_offline_alerts_created_at
                ON offline_alerts(created_at)
                """
            )
            connection.commit()

    def enqueue(
        self,
        alert_id: str,
        event_kind: str,
        payload: dict[str, Any],
        snapshot_path: str | None = None,
    ) -> bool:
        """Stores an alert in the offline queue.

        Args:
            alert_id: Unique identifier of the alert.
            event_kind: Kind of event ('match_confirmed' or 'rule_triggered').
            payload: JSON-serializable alert payload dictionary.
            snapshot_path: Path to snapshot image on local disk.

        Returns:
            True if inserted, False if alert_id was already present.
        """
        created_at = datetime.now(UTC).isoformat()
        payload_text = json.dumps(payload)
        try:
            with self._get_connection() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO offline_alerts (
                        alert_id, event_kind, payload, snapshot_path, created_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(alert_id) DO NOTHING
                    """,
                    (alert_id, event_kind, payload_text, snapshot_path, created_at),
                )
                connection.commit()
                if cursor.rowcount <= 0:
                    return False
                logger.info("Enqueued offline alert %s (%s)", alert_id, event_kind)
                return True
        except Exception as error:
            logger.error("Failed to enqueue offline alert %s: %s", alert_id, error)
            return False

    def peek(self, limit: int = 50) -> list[OfflineAlertItem]:
        """Returns the oldest unacknowledged alerts from the queue up to limit."""
        with self._get_connection() as connection:
            cursor = connection.execute(
                """
                SELECT id, alert_id, event_kind, payload, snapshot_path, created_at, retry_count
                FROM offline_alerts
                ORDER BY id ASC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()

        items: list[OfflineAlertItem] = []
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except Exception:
                payload = {}
            items.append(
                OfflineAlertItem(
                    id=row["id"],
                    alert_id=row["alert_id"],
                    event_kind=row["event_kind"],
                    payload=payload,
                    snapshot_path=row["snapshot_path"],
                    created_at=row["created_at"],
                    retry_count=row["retry_count"],
                )
            )
        return items

    def acknowledge(self, alert_ids: Sequence[str]) -> int:
        """Removes acknowledged alerts from the queue after successful upload.

        Returns:
            Number of removed alerts.
        """
        if not alert_ids:
            return 0
        placeholders = ",".join("?" for _ in alert_ids)
        with self._get_connection() as connection:
            cursor = connection.execute(
                f"DELETE FROM offline_alerts WHERE alert_id IN ({placeholders})",  # noqa: S608
                tuple(alert_ids),
            )
            connection.commit()
            removed_count = cursor.rowcount
            logger.info("Acknowledged and removed %d alerts from offline queue", removed_count)
            return removed_count

    def increment_retry(self, alert_ids: Sequence[str]) -> None:
        """Increments the retry counter for the specified alerts."""
        if not alert_ids:
            return
        placeholders = ",".join("?" for _ in alert_ids)
        query = (
            f"UPDATE offline_alerts SET retry_count = retry_count + 1 "  # noqa: S608
            f"WHERE alert_id IN ({placeholders})"
        )
        with self._get_connection() as connection:
            connection.execute(query, tuple(alert_ids))
            connection.commit()

    def count(self) -> int:
        """Returns total number of queued offline alerts."""
        with self._get_connection() as connection:
            cursor = connection.execute("SELECT COUNT(*) AS total FROM offline_alerts")
            row = cursor.fetchone()
            return int(row["total"]) if row else 0
