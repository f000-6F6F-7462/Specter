import shutil
from pathlib import Path

import pytest
from peewee import IntegrityError, SqliteDatabase
from playhouse.migrations import Runner

from specter.storage.database import open_database
from specter.storage.migrate import MIGRATIONS_DIRECTORY, apply_migrations
from specter.storage.tables import CameraWatchlistRecord

EXPECTED_TABLE_NAMES = {
    "cameras",
    "watchlists",
    "camera_watchlists",
    "targets",
    "reference_images",
    "image_embeddings",
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

    assert first_run_names == [
        "0001_initial",
        "0002_matching_per_modality",
        "0003_image_embeddings",
        "0004_camera_transport",
        "0005_camera_metadata",
    ]
    assert second_run_names == []


@pytest.mark.usefixtures("database")
def test_row_is_rejected_when_it_refers_to_a_missing_camera() -> None:
    with pytest.raises(IntegrityError):
        CameraWatchlistRecord.insert(
            camera_id="camera_missing", watchlist_id="watchlist_missing", position=0
        ).execute()


def test_image_status_becomes_face_and_pending_appearance_when_embeddings_are_split(
    tmp_path: Path,
) -> None:
    earlier_migrations_directory = tmp_path / "earlier_migrations"
    earlier_migrations_directory.mkdir()
    for migration_name in ("0001_initial.py", "0002_matching_per_modality.py"):
        shutil.copy(MIGRATIONS_DIRECTORY / migration_name, earlier_migrations_directory)
    database = open_database(tmp_path / "specter.sqlite3")
    Runner(database, str(earlier_migrations_directory)).up()  # type: ignore[no-untyped-call]
    for statement in (
        "INSERT INTO watchlists VALUES ('watchlist_1', 'owner_alice', 'Wanted', 'person', "
        "'blacklist', '{}', 'created', 0.45, 0.75)",
        "INSERT INTO targets VALUES ('target_jane', 'watchlist_1', 'Jane', 'person', 1, '{}', "
        "NULL, 'created')",
        "INSERT INTO reference_images VALUES ('image_jane', 'target_jane', 'images/jane.jpg', "
        "'embedded', NULL, NULL, 'arcface_r50', 'created')",
    ):
        database.execute_sql(statement)  # type: ignore[no-untyped-call]

    apply_migrations(database)
    rows = database.execute_sql(  # type: ignore[no-untyped-call]
        "SELECT reference_image_id, modality, status, model_version FROM image_embeddings "
        "ORDER BY modality"
    ).fetchall()
    database.close()

    assert rows == [
        ("image_jane", "appearance", "pending", None),
        ("image_jane", "face", "embedded", "arcface_r50"),
    ]
