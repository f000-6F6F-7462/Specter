"""The SQLite database, and the thread that all database work of a process runs on."""

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from peewee import DatabaseProxy, SqliteDatabase

from specter.storage.tables import RECORD_TYPES

DATABASE_THREAD_NAME = "specter-database"
BUSY_TIMEOUT_MILLISECONDS = 5000

# WAL lets the API, camera manager and detector read while another process writes, and the busy
# timeout makes a second writer wait instead of failing immediately.
SQLITE_PRAGMAS = {
    "journal_mode": "wal",
    "synchronous": "normal",
    "foreign_keys": 1,
    "busy_timeout": BUSY_TIMEOUT_MILLISECONDS,
}

# Query modules open transactions through this proxy, so they never need the database passed in.
database_proxy = DatabaseProxy()


def open_database(database_file: Path) -> SqliteDatabase:
    """Opens the database file, creating its directory if needed, and binds every table to it."""
    database_file.parent.mkdir(parents=True, exist_ok=True)
    database = SqliteDatabase(str(database_file), pragmas=SQLITE_PRAGMAS)
    database.bind(RECORD_TYPES)
    database_proxy.initialize(database)
    return database


class DatabaseThread:
    """Runs synchronous database work on one dedicated thread, off the asyncio event loop.

    A single thread gives the process a single SQLite connection and one writer at a time.
    """

    def __init__(self, database: SqliteDatabase) -> None:
        self._database = database
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=DATABASE_THREAD_NAME)

    async def run[ResultT](self, operation: Callable[[], ResultT]) -> ResultT:
        """Runs the operation on the database thread and returns its result."""
        return await asyncio.get_running_loop().run_in_executor(self._executor, operation)

    def close(self) -> None:
        """Waits for pending work, closes the thread's connection and stops the thread."""
        self._executor.submit(self._database.close).result()
        self._executor.shutdown(wait=True)
