"""Removes the camera transport setting, which nothing used.

go2rtc connects to cameras itself, and its RTSP client offers no choice of transport, so the
stored value never changed how a camera was read.
"""

from peewee import Database
from playhouse.migrate import SchemaMigrator, migrate


def up(migrator: SchemaMigrator, database: Database) -> None:
    """Drops the transport column of cameras."""
    with database.atomic():
        migrate(migrator.drop_column("cameras", "transport"))  # type: ignore[no-untyped-call]
