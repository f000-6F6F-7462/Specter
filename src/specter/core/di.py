"""Composition root.

Selectors on ``Settings`` (``bus`` / ``blob`` / ``vectors`` / ``inference``) pick the
implementation; ``memory`` / ``fake`` are used by tests and by ``SPECTER_ENV=local``
runs without the backing services.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from specter.application.ports import (
    BlobStore,
    EventBus,
    FaceEmbeddingService,
    UnitOfWork,
    UnitOfWorkFactory,
    VectorIndex,
)
from specter.core.clock import Clock, SystemClock
from specter.core.logging import configure_logging
from specter.core.settings import Settings
from specter.infrastructure.blob.memory import MemoryBlobStore
from specter.infrastructure.bus.memory import MemoryBus
from specter.infrastructure.db import create_engine, session_factory
from specter.infrastructure.db.uow import SqlAlchemyUnitOfWork
from specter.infrastructure.ml.fakes import FakeFaceEmbeddingService
from specter.infrastructure.vectors.memory import InMemoryVectorIndex


@dataclass(frozen=True, slots=True)
class Container:
    settings: Settings
    clock: Clock
    engine: AsyncEngine
    uow_factory: UnitOfWorkFactory
    blob: BlobStore
    bus: EventBus
    vectors: VectorIndex
    faces: FaceEmbeddingService


def _build_blob(settings: Settings) -> BlobStore:
    if settings.blob == "minio":
        from specter.infrastructure.blob.minio import MinioBlobStore

        return MinioBlobStore(settings.s3)
    return MemoryBlobStore()


def _build_bus(settings: Settings) -> EventBus:
    if settings.bus == "redis":
        from specter.infrastructure.bus.redis_stream import RedisStreamBus

        return RedisStreamBus(settings.redis.url, maxlen=settings.redis.stream_maxlen)
    return MemoryBus()


def _build_vectors(settings: Settings) -> VectorIndex:
    if settings.vectors == "qdrant":
        from specter.infrastructure.vectors.qdrant import QdrantVectorIndex

        return QdrantVectorIndex(settings.qdrant.url)
    return InMemoryVectorIndex()


def _build_faces(settings: Settings) -> FaceEmbeddingService:
    if settings.inference == "insightface":
        from specter.infrastructure.ml.insightface import InsightFaceEmbeddingService

        return InsightFaceEmbeddingService()
    return FakeFaceEmbeddingService()


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
        bus=_build_bus(settings),
        vectors=_build_vectors(settings),
        faces=_build_faces(settings),
    )
