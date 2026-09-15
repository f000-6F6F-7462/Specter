from collections.abc import Iterator
from pathlib import Path

import pytest
from peewee import SqliteDatabase

from specter.storage.credentials import CredentialCipher
from specter.storage.database import open_database
from specter.storage.migrate import apply_migrations


@pytest.fixture
def database(tmp_path: Path) -> Iterator[SqliteDatabase]:
    opened_database = open_database(tmp_path / "specter.sqlite3")
    apply_migrations(opened_database)
    yield opened_database
    opened_database.close()


@pytest.fixture
def cipher() -> CredentialCipher:
    return CredentialCipher(CredentialCipher.generate_key())
