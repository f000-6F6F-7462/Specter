"""Adds free-form metadata to cameras, which apps use to keep their own fields with the camera."""

from peewee import Database, TextField
from playhouse.migrate import SchemaMigrator, migrate


def up(migrator: SchemaMigrator, database: Database) -> None:
    """Adds the metadata column of cameras, empty for existing cameras."""
    with database.atomic():
        migrate(  # type: ignore[no-untyped-call]
            migrator.add_column("cameras", "metadata_json", TextField(default="{}")),
        )
