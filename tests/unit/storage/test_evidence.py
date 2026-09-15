from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from specter.core.errors import NotFoundError
from specter.storage.evidence import EvidenceStore

SNAPSHOT_SIZE_BYTES = 100


def write_snapshot_on(store: EvidenceStore, captured_on: date, alert_id: str) -> str:
    captured_at = datetime(captured_on.year, captured_on.month, captured_on.day, 12, tzinfo=UTC)
    snapshot_path = store.build_snapshot_path("owner_alice", alert_id, captured_at)
    store.write_snapshot(snapshot_path, b"\xff" * SNAPSHOT_SIZE_BYTES)
    return snapshot_path


def test_snapshot_path_is_grouped_by_owner_and_utc_day_when_built(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)

    snapshot_path = store.build_snapshot_path(
        "owner_alice", "alert_1", datetime(2026, 9, 15, 23, 30, tzinfo=UTC)
    )

    assert snapshot_path == "evidence/owner_alice/2026/09/15/alert_1.jpg"


def test_snapshot_path_is_rejected_when_id_could_escape_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot be used in an evidence path"):
        EvidenceStore(tmp_path).build_snapshot_path("..", "alert_1", datetime.now(UTC))


def test_written_snapshot_can_be_resolved_when_it_exists(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)

    snapshot_path = write_snapshot_on(store, date(2026, 9, 15), "alert_1")

    assert store.resolve_snapshot_file(snapshot_path).read_bytes() == b"\xff" * SNAPSHOT_SIZE_BYTES


def test_resolving_fails_when_path_points_outside_evidence(tmp_path: Path) -> None:
    (tmp_path / "specter.sqlite3").write_bytes(b"database")

    with pytest.raises(NotFoundError, match="outside the evidence directory"):
        EvidenceStore(tmp_path).resolve_snapshot_file("evidence/../specter.sqlite3")


def test_retention_removes_days_older_than_maximum_age(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    write_snapshot_on(store, date(2026, 8, 1), "alert_old")
    recent_snapshot_path = write_snapshot_on(store, date(2026, 9, 14), "alert_recent")

    report = store.enforce_retention(
        today=date(2026, 9, 15), maximum_age_days=30, maximum_size_bytes=10_000
    )

    assert report.removed_day_directories == ("evidence/owner_alice/2026/08/01",)
    assert store.resolve_snapshot_file(recent_snapshot_path).is_file()


def test_retention_removes_oldest_days_when_quota_is_exceeded(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path)
    for day_number in (13, 14, 15):
        write_snapshot_on(store, date(2026, 9, day_number), f"alert_{day_number}")

    report = store.enforce_retention(
        today=date(2026, 9, 15),
        maximum_age_days=30,
        maximum_size_bytes=2 * SNAPSHOT_SIZE_BYTES,
    )

    assert report.removed_day_directories == ("evidence/owner_alice/2026/09/13",)
    assert report.remaining_size_bytes == 2 * SNAPSHOT_SIZE_BYTES
