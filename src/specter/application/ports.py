"""
Application Protocols.
"""

from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

import numpy as np

from specter.contracts import BrokerMessage
from specter.domain.alerts import Alert, Disposition
from specter.domain.catalog import EnrollmentStatus, Target, Watchlist
from specter.domain.matching import Candidate
from specter.domain.quality import QualityReport, RejectionReason
from specter.domain.streams import StreamConfig, StreamHealth
from specter.domain.vision import Crop, Detection, Embedding, Frame, Track, Vector

# media / ML


@runtime_checkable
class FrameSource(Protocol):
    """A live video connection. Iterating yields the freshest decoded frame."""

    def __aiter__(self) -> AsyncIterator[Frame]: ...

    async def aclose(self) -> None: ...


type FrameSourceFactory = Callable[[StreamConfig], FrameSource]


@runtime_checkable
class Detector(Protocol):
    async def detect(self, frames: Sequence[Frame]) -> list[list[Detection]]: ...


@runtime_checkable
class Tracker(Protocol):
    def update(self, stream_id: str, detections: Sequence[Detection]) -> list[Track]: ...

    def forget(self, stream_id: str) -> None:
        """Drop a stream's tracking state when its pipeline stops."""


@runtime_checkable
class Embedder(Protocol):
    modality: str

    async def embed(self, crops: Sequence[Crop]) -> list[Embedding]: ...


@runtime_checkable
class FrameCodec(Protocol):
    """Serialise an evidence frame for the blob store."""

    extension: str
    content_type: str

    def encode(self, image: np.ndarray) -> bytes: ...


@dataclass(frozen=True, slots=True)
class ReferenceEmbedding:
    """Outcome of running one reference image through detect -> quality -> align -> embed.

    ``vector`` is set only when a single acceptable face was found; otherwise
    ``rejection`` says why.
    """

    vector: Vector | None = None
    quality: QualityReport | None = None
    rejection: RejectionReason | None = None
    faces_found: int = 0


@runtime_checkable
class FaceEmbeddingService(Protocol):
    """Batch (enrollment) face encoder: image bytes -> a normalised 512-d vector."""

    model_version: str

    async def embed_reference(self, image: bytes) -> ReferenceEmbedding: ...


#  vector store


@runtime_checkable
class VectorIndex(Protocol):
    async def upsert(self, points: Sequence[Embedding]) -> None: ...

    async def search(
        self,
        modality: str,
        query: Vector,
        *,
        owner_id: str,
        watchlist_ids: Sequence[str],
        top_k: int = 5,
    ) -> list[Candidate]: ...

    async def delete(self, *, target_id: str) -> None: ...


#  blob store


@runtime_checkable
class BlobStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> str: ...

    async def get(self, key: str) -> bytes: ...

    async def presigned_url(self, key: str, *, ttl_s: int = 900) -> str: ...


#  event bus


@runtime_checkable
class Delivery(Protocol):
    id: str
    message: BrokerMessage

    async def ack(self) -> None: ...


@runtime_checkable
class EventBus(Protocol):
    """Redis Streams: XADD to publish, XREADGROUP/XACK to consume."""

    async def publish(self, stream: str, key: str, message: BrokerMessage) -> None: ...

    def consume(self, stream: str, *, group: str, consumer: str) -> AsyncIterator[Delivery]: ...


# ephemeral kv


@runtime_checkable
class HealthStore(Protocol):
    """Redis KV: per-stream health hashes (TTL) and match cooldown keys."""

    async def set_health(
        self, stream_id: str, health: StreamHealth, *, ttl_s: int = 10
    ) -> None: ...

    async def get_health(self, stream_id: str) -> StreamHealth | None: ...

    async def in_cooldown(self, key: str) -> bool: ...

    async def mark_cooldown(self, key: str, *, ttl_s: int) -> None: ...

    async def bump_watchlist_version(self, watchlist_id: str) -> int: ...


#  repositories


@runtime_checkable
class WatchlistRepo(Protocol):
    async def get(self, watchlist_id: str) -> Watchlist | None: ...

    async def list_for_owner(self, owner_id: str) -> list[Watchlist]: ...

    async def add(self, watchlist: Watchlist) -> None: ...

    async def update(self, watchlist: Watchlist) -> None: ...

    async def soft_delete(self, watchlist_id: str) -> None: ...


@runtime_checkable
class TargetRepo(Protocol):
    async def get(self, target_id: str) -> Target | None: ...

    async def list_for_watchlist(
        self, watchlist_id: str, *, status: EnrollmentStatus | None = None
    ) -> list[Target]: ...

    async def list_by_batch(self, batch_id: str) -> list[Target]: ...

    async def count_for_watchlist(self, watchlist_id: str) -> int: ...

    async def add(self, target: Target) -> None: ...

    async def update(self, target: Target) -> None: ...

    async def soft_delete(self, target_id: str) -> None: ...


@runtime_checkable
class StreamRepo(Protocol):
    async def get(self, stream_id: str) -> StreamConfig | None: ...

    async def list_for_owner(self, owner_id: str) -> list[StreamConfig]: ...

    async def list_enabled(self) -> list[StreamConfig]: ...

    async def add(self, stream: StreamConfig) -> None: ...

    async def update(self, stream: StreamConfig) -> None: ...

    async def delete(self, stream_id: str) -> None: ...


@runtime_checkable
class AlertRepo(Protocol):
    async def get(self, alert_id: str) -> Alert | None: ...

    async def add(self, alert: Alert) -> None: ...

    async def list_for_owner(
        self,
        owner_id: str,
        *,
        stream_id: str | None = None,
        watchlist_id: str | None = None,
        disposition: Disposition | None = None,
        min_confidence: float | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> list[Alert]: ...

    async def update(self, alert: Alert) -> None: ...


#  unit of work


@runtime_checkable
class UnitOfWork(Protocol):
    watchlists: WatchlistRepo
    targets: TargetRepo
    streams: StreamRepo
    alerts: AlertRepo

    async def __aenter__(self) -> "UnitOfWork": ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


type UnitOfWorkFactory = Callable[[], UnitOfWork]
