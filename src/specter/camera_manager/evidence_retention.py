"""Removes old evidence snapshots on a schedule, keeping the evidence within its age and quota."""

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime
from functools import partial

from specter.config.settings import EvidenceSettings
from specter.storage.alerts import clear_snapshot_paths_under
from specter.storage.database import DatabaseThread
from specter.storage.evidence import EvidenceStore

logger = logging.getLogger(__name__)

# Days are removed whole, so checking more often than hourly frees nothing sooner, while a full
# quota is noticed well before a busy day can fill the disk.
RETENTION_INTERVAL_SECONDS = 3600.0


class EvidenceRetention:
    """Enforces evidence retention at startup and then every hour.

    Alerts that pointed at a removed snapshot forget its path, so the API reports the snapshot as
    gone instead of failing to read it. A failed pass is logged and retried at the next interval.
    """

    def __init__(
        self,
        evidence_settings: EvidenceSettings,
        evidence_store: EvidenceStore,
        database_thread: DatabaseThread,
    ) -> None:
        self._evidence_settings = evidence_settings
        self._evidence_store = evidence_store
        self._database_thread = database_thread

    async def run(self, shutdown_requested: asyncio.Event) -> None:
        """Enforces retention on schedule until shutdown is requested."""
        while not shutdown_requested.is_set():
            try:
                await self.enforce()
            except OSError:
                logger.exception("cannot enforce evidence retention")
            with suppress(TimeoutError):
                await asyncio.wait_for(shutdown_requested.wait(), RETENTION_INTERVAL_SECONDS)

    async def enforce(self) -> None:
        """Removes expired and excess days of evidence, and the alerts' paths into them."""
        report = await asyncio.to_thread(
            self._evidence_store.enforce_retention,
            datetime.now(UTC).date(),
            self._evidence_settings.maximum_age_days,
            self._evidence_settings.maximum_size_bytes,
        )
        cleared_alert_count = 0
        for day_directory in report.removed_day_directories:
            cleared_alert_count += await asyncio.get_running_loop().run_in_executor(
                self._database_thread.executor, partial(clear_snapshot_paths_under, day_directory)
            )
        if report.removed_day_directories:
            logger.info(
                "evidence removed",
                extra={
                    "removed_day_directories": list(report.removed_day_directories),
                    "cleared_alert_count": cleared_alert_count,
                    "remaining_size_bytes": report.remaining_size_bytes,
                },
            )
