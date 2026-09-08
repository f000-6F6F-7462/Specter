"""Composition root.

Selectors on ``Settings`` (``bus`` / ``blob`` / ``vectors`` / ``inference`` / ``media``)
pick the implementation; ``memory`` / ``fake`` / ``synthetic`` are used by tests and by
``SPECTER_ENV=local`` runs without the backing services and model weights.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from specter.application.pipeline import PipelineTuning
from specter.application.ports import (
    BlobStore,
    Detector,
    Embedder,
    EventBus,
    FaceEmbeddingService,
    FrameSource,
    FrameSourceFactory,
    Tracker,
    UnitOfWork,
    UnitOfWorkFactory,
    VectorIndex,
)
from specter.core.clock import Clock, SystemClock
from specter.core.errors import ConfigurationError
from specter.core.logging import configure_logging
from specter.core.settings import Settings
from specter.domain.streams import StreamConfig
from specter.infrastructure.blob.memory import MemoryBlobStore
from specter.infrastructure.bus.memory import MemoryBus
from specter.infrastructure.db import create_engine, session_factory
from specter.infrastructure.db.uow import SqlAlchemyUnitOfWork
from specter.infrastructure.media.fakes import SyntheticFrameSource
from specter.infrastructure.ml.detector import FakeDetector
from specter.infrastructure.ml.embedder import FakeEmbedder
from specter.infrastructure.ml.fakes import FakeFaceEmbeddingService
from specter.infrastructure.ml.tracker import IouTracker
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
    frame_source_factory: FrameSourceFactory
    detector: Detector
    tracker: Tracker
    embedders: Mapping[str, Embedder]
    pipeline_tuning: PipelineTuning


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


def _build_frame_source_factory(settings: Settings) -> FrameSourceFactory:
    if settings.media == "synthetic":

        def factory(stream: StreamConfig) -> FrameSource:
            return SyntheticFrameSource(
                stream.id, count=None, fps=stream.sampling.target_fps, moving=True
            )

        return factory
    raise ConfigurationError(
        "GStreamer capture lands in Phase 5 — set SPECTER_MEDIA=synthetic to run now"
    )


def _build_detector(settings: Settings) -> Detector:
    impl = settings.models.detector.impl
    if impl == "fake":
        return FakeDetector()
    raise ConfigurationError(
        f"detector impl {impl!r} lands in Phase 5 — set models.detector.impl=fake to run now"
    )


def _build_embedders(settings: Settings) -> dict[str, Embedder]:
    embedders: dict[str, Embedder] = {}
    for modality, cfg in settings.models.embedders.items():
        if cfg.impl == "fake":
            embedders[modality] = FakeEmbedder(modality)
        else:
            raise ConfigurationError(
                f"embedder impl {cfg.impl!r} for {modality!r} lands in Phase 5 — "
                f"set models.embedders.{modality}.impl=fake to run now"
            )
    return embedders


def _pipeline_tuning(settings: Settings) -> PipelineTuning:
    p = settings.pipeline
    return PipelineTuning(
        queue_size=p.queue_size,
        directory_refresh_s=p.directory_refresh_s,
        top_k=p.top_k,
        min_detection_confidence=p.min_detection_confidence,
        need=p.need,
        window=p.window,
        cooldown_s=p.cooldown_s,
        motion_min_delta=p.motion_min_delta,
        capture_evidence=p.capture_evidence,
        evidence_ttl_s=p.evidence_ttl_s,
    )


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
        frame_source_factory=_build_frame_source_factory(settings),
        detector=_build_detector(settings),
        tracker=IouTracker(),
        embedders=_build_embedders(settings),
        pipeline_tuning=_pipeline_tuning(settings),
    )
