from pathlib import Path

import pytest
from peewee import IntegrityError, SqliteDatabase

from specter.storage.database import open_database
from specter.storage.migrate import apply_migrations
from specter.storage.tables import CameraWatchlistRecord

EXPECTED_TABLE_NAMES = {
    "cameras",
    "watchlists",
    "camera_watchlists",
    "targets",
    "reference_images",
    "zones",
    "zone_occupancy_rules",
    "line_crossing_rules",
    "identity_match_alerts",
    "rule_alerts",
}


def test_every_table_exists_when_migrations_are_applied(database: SqliteDatabase) -> None:
    assert set(database.get_tables()) >= EXPECTED_TABLE_NAMES


def test_nothing_is_applied_when_migrations_run_again(tmp_path: Path) -> None:
    database = open_database(tmp_path / "specter.sqlite3")

    first_run_names = apply_migrations(database)
    second_run_names = apply_migrations(database)
    database.close()

    assert first_run_names == ["0001_initial", "0002_matching_per_modality"]
    assert second_run_names == []


@pytest.mark.usefixtures("database")
def test_row_is_rejected_when_it_refers_to_a_missing_camera() -> None:
    with pytest.raises(IntegrityError):
        CameraWatchlistRecord.insert(
            camera_id="camera_missing", watchlist_id="watchlist_missing", position=0
        ).execute()
