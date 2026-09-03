"""Composition root.

The one place concrete adapters are chosen and wired.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from specter.application.ports import BlobStore, UnitOfWork, UnitOfWorkFactory
from specter.core.clock import Clock, SystemClock
from specter.core.logging import configure_logging
from specter.core.settings import Settings
from specter.infrastructure.blob.memory import MemoryBlobStore
from specter.infrastructure.db import create_engine, session_factory
from specter.infrastructure.db.uow import SqlAlchemyUnitOfWork


@dataclass(frozen=True, slots=True)
class Container:
    settings: Settings
    clock: Clock
    engine: AsyncEngine
    uow_factory: UnitOfWorkFactory
    blob: BlobStore


def _build_blob(settings: Settings) -> BlobStore:
    _ = settings  # TODO implement miniIO.
    return MemoryBlobStore()


def build_container(settings: Settings | None = None) -> Container:
    settings = settings or Settings()
    configure_logging(settings.log.level, json_output=settings.log.json_output)

    engine = create_engine(settings.database.url)
    sessions = session_factory(engine)

    def uow_factory() -> UnitOfWork:
        return SqlAlchemyUnitOfWork(sessions)

    return Container(
        settings=settings,
        clock=SystemClock(),
        engine=engine,
        uow_factory=uow_factory,
        blob=_build_blob(settings),
    )
