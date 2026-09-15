"""Applies pending database migrations."""

from pathlib import Path

from peewee import Database
from playhouse.migrations import Runner

from specter.storage.database import open_database

MIGRATIONS_DIRECTORY = Path(__file__).parent / "migrations"


def apply_migrations(database: Database) -> list[str]:
    """Applies every pending migration in order and returns the names of the applied ones."""
    # playhouse.migrations ships without type information.
    migration_runner = Runner(database, str(MIGRATIONS_DIRECTORY))  # type: ignore[no-untyped-call]
    applied_migration_names: list[str] = migration_runner.up()  # type: ignore[no-untyped-call]
    return applied_migration_names


def migrate_database_file(database_file: Path) -> list[str]:
    """Opens the database file, applies every pending migration and closes it again."""
    database = open_database(database_file)
    try:
        return apply_migrations(database)
    finally:
        database.close()
