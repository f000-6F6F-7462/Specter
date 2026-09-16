from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from peewee import SqliteDatabase

from specter.camera_manager.evidence_retention import EvidenceRetention
from specter.config.settings import EvidenceSettings
from specter.entities.alerts import RuleAlert
from specter.entities.geometry import NormalizedBoundingBox
from specter.entities.rules import RuleKind
from specter.storage.alerts import get_alert, save_alert
from specter.storage.database import DatabaseThread, open_database
from specter.storage.evidence import EvidenceStore
from specter.storage.migrate import apply_migrations

MAXIMUM_AGE_DAYS = 30


@pytest.fixture
def database(tmp_path: Path) -> Iterator[SqliteDatabase]:
    opened_database = open_database(tmp_path / "specter.sqlite3")
    apply_migrations(opened_database)
    yield opened_database
    opened_database.close()


def save_alert_with_snapshot(store: EvidenceStore, alert_id: str, captured_at: datetime) -> str:
    snapshot_path = store.build_snapshot_path("owner_alice", alert_id, captured_at)
    store.write_snapshot(snapshot_path, b"\xff\xd8")
    save_alert(
        RuleAlert(
            id=alert_id,
            owner_id="owner_alice",
            camera_id="camera_front_door",
            track_id=1,
            rule_id="rule_door",
            rule_kind=RuleKind.ZONE_OCCUPANCY,
            zone_id="zone_door",
            object_class="person",
            bounding_box=NormalizedBoundingBox(x=0.1, y=0.1, width=0.2, height=0.5),
            dwell_seconds=3.0,
            crossing_direction=None,
            frame_captured_at=captured_at,
            created_at=captured_at,
            snapshot_path=snapshot_path,
        )
    )
    return snapshot_path


async def test_expired_snapshots_are_removed_and_alerts_forget_them(
    tmp_path: Path, database: SqliteDatabase
) -> None:
    store = EvidenceStore(tmp_path / "data")
    now = datetime.now(UTC)
    old_snapshot_path = save_alert_with_snapshot(
        store, "alert_old", now - timedelta(days=MAXIMUM_AGE_DAYS + 2)
    )
    recent_snapshot_path = save_alert_with_snapshot(store, "alert_recent", now)
    database_thread = DatabaseThread(database)
    retention = EvidenceRetention(
        EvidenceSettings(maximum_age_days=MAXIMUM_AGE_DAYS), store, database_thread
    )

    try:
        await retention.enforce()
        old_alert = get_alert("alert_old")
        recent_alert = get_alert("alert_recent")
    finally:
        database_thread.executor.shutdown(wait=True)

    assert not (tmp_path / "data" / old_snapshot_path).exists()
    assert (tmp_path / "data" / recent_snapshot_path).exists()
    assert old_alert.snapshot_path is None
    assert recent_alert.snapshot_path == recent_snapshot_path
