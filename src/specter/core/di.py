"""Composition root.

Selectors on ``Settings`` (``bus`` / ``blob`` / ``vectors`` / ``inference`` / ``media``,
plus ``models.detector.impl`` / ``models.embedders.*.impl`` / ``pipeline.evidence_format``)
pick the implementation; ``memory`` / ``fake`` / ``synthetic`` / ``npy`` are used by tests
and by ``SPECTER_ENV=local`` runs without the backing services and model weights.
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
    FrameCodec,
    FrameSource,
    FrameSourceFactory,
    HealthStore,
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
from specter.infrastructure.health.memory import InMemoryHealthStore
from specter.infrastructure.media.codec import NumpyFrameCodec
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
    health: HealthStore
    vectors: VectorIndex
    faces: FaceEmbeddingService
    frame_source_factory: FrameSourceFactory
    detector: Detector
    tracker: Tracker
    embedders: Mapping[str, Embedder]
    codec: FrameCodec
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


def _build_health(settings: Settings, clock: Clock) -> HealthStore:
    # Same selector as the bus — one Redis instance for messaging *and* caching.
    if settings.bus == "redis":
        from specter.infrastructure.health.redis import RedisHealthStore

        return RedisHealthStore(settings.redis.url)
    return InMemoryHealthStore(clock)


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

        def synthetic(stream: StreamConfig) -> FrameSource:
            return SyntheticFrameSource(
                stream.id, count=None, fps=stream.sampling.target_fps, moving=True
            )

        return synthetic
    if settings.media == "gstreamer":
        from specter.infrastructure.media.gstreamer import GStreamerFrameSource

        def gstreamer(stream: StreamConfig) -> FrameSource:
            return GStreamerFrameSource(stream.id, stream.source)

        return gstreamer
    raise ConfigurationError(
        f"unknown media source {settings.media!r} — use 'gstreamer' or 'synthetic'"
    )


def _build_detector(settings: Settings) -> Detector:
    cfg = settings.models.detector
    if cfg.impl == "fake":
        return FakeDetector()
    if cfg.impl == "yolo":
        from specter.infrastructure.ml.yolo_detector import YoloDetector

        return YoloDetector(cfg)
    raise ConfigurationError(f"unknown detector impl {cfg.impl!r} — use 'yolo' or 'fake'")


def _build_embedders(settings: Settings) -> dict[str, Embedder]:
    embedders: dict[str, Embedder] = {}
    for modality, cfg in settings.models.embedders.items():
        if cfg.impl == "fake":
            embedders[modality] = FakeEmbedder(modality)
        elif cfg.impl == "insightface":
            from specter.infrastructure.ml.face_embedder import FaceEmbedder

            embedders[modality] = FaceEmbedder(model_name=cfg.name or "buffalo_l")
        else:
            raise ConfigurationError(
                f"unknown embedder impl {cfg.impl!r} for {modality!r} — "
                f"use 'insightface' or 'fake'"
            )
    return embedders


def _build_codec(settings: Settings) -> FrameCodec:
    fmt = settings.pipeline.evidence_format
    if fmt == "npy":
        return NumpyFrameCodec()
    if fmt == "jpeg":
        from specter.infrastructure.ml.jpeg_codec import JpegFrameCodec

        return JpegFrameCodec()
    raise ConfigurationError(f"unknown evidence_format {fmt!r} — use 'jpeg' or 'npy'")


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
        health_publish_interval_s=p.health_publish_interval_s,
        health_ttl_s=p.health_ttl_s,
    )


def build_container(settings: Settings | None = None) -> Container:
    settings = settings or Settings()
    configure_logging(settings.log.level, json_output=settings.log.json_output)

    engine = create_engine(settings.database.url)
    sessions = session_factory(engine)
    clock = SystemClock()

    def uow_factory() -> UnitOfWork:
        return SqlAlchemyUnitOfWork(sessions)

    return Container(
        settings=settings,
        clock=clock,
        engine=engine,
        uow_factory=uow_factory,
        blob=_build_blob(settings),
        bus=_build_bus(settings),
        health=_build_health(settings, clock),
        vectors=_build_vectors(settings),
        faces=_build_faces(settings),
        frame_source_factory=_build_frame_source_factory(settings),
        detector=_build_detector(settings),
        tracker=IouTracker(),
        embedders=_build_embedders(settings),
        codec=_build_codec(settings),
        pipeline_tuning=_pipeline_tuning(settings),
    )
