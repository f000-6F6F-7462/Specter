"""Pydantic request/response models for the REST API."""

from dataclasses import asdict
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from specter.application.alerts import AlertPage, AlertView
from specter.application.catalog import (
    EnrollmentBatchView,
    EnrollTargetsResult,
    ReferenceImageView,
    TargetView,
    WatchlistView,
)
from specter.application.ports import BlobStore
from specter.application.streams import StreamView
from specter.core.errors import NotFoundError
from specter.domain.alerts import Disposition
from specter.domain.catalog import EnrollmentStatus, ImageStatus, TargetType, WatchlistKind
from specter.domain.streams import (
    DesiredState,
    SamplingMode,
    StreamHealth,
    StreamProtocol,
    StreamStatus,
    TransportProtocol,
)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# watchlists


class WatchlistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    type: TargetType
    kind: WatchlistKind = WatchlistKind.WATCHLIST
    match_threshold: float = Field(default=0.78, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WatchlistUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    match_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    metadata: dict[str, Any] | None = None


class WatchlistOut(_Out):
    id: str
    owner_id: str
    name: str
    type: TargetType
    kind: WatchlistKind
    match_threshold: float
    metadata: dict[str, Any]
    target_count: int

    @classmethod
    def of(cls, view: WatchlistView) -> "WatchlistOut":
        return cls.model_validate(view)


# targets


class TargetSpecIn(BaseModel):
    ref: str = Field(min_length=1)
    label: str = Field(min_length=1, max_length=255)
    type: TargetType
    image_names: list[str] = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TargetUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=255)
    enabled: bool | None = None
    metadata: dict[str, Any] | None = None


class ReferenceImageOut(_Out):
    id: str
    blob_key: str
    status: ImageStatus
    rejection_reason: str | None
    model_version: str | None
    quality: dict[str, Any] | None

    @classmethod
    def of(cls, view: ReferenceImageView) -> "ReferenceImageOut":
        return cls(
            id=view.id,
            blob_key=view.blob_key,
            status=view.status,
            rejection_reason=view.rejection_reason.value if view.rejection_reason else None,
            model_version=view.model_version,
            quality=asdict(view.quality) if view.quality else None,
        )


class TargetOut(_Out):
    id: str
    watchlist_id: str
    label: str
    type: TargetType
    enabled: bool
    status: EnrollmentStatus
    batch_id: str | None
    metadata: dict[str, Any]
    images: list[ReferenceImageOut]

    @classmethod
    def of(cls, view: TargetView) -> "TargetOut":
        return cls(
            id=view.id,
            watchlist_id=view.watchlist_id,
            label=view.label,
            type=view.type,
            enabled=view.enabled,
            status=view.status,
            batch_id=view.batch_id,
            metadata=dict(view.metadata),
            images=[ReferenceImageOut.of(i) for i in view.images],
        )


class EnrolledTargetOut(BaseModel):
    ref: str
    target_id: str
    status: EnrollmentStatus
    image_ids: list[str]


class EnrollOut(BaseModel):
    batch_id: str
    items: list[EnrolledTargetOut]

    @classmethod
    def of(cls, result: EnrollTargetsResult) -> "EnrollOut":
        return cls(
            batch_id=result.batch_id,
            items=[
                EnrolledTargetOut(
                    ref=i.ref,
                    target_id=i.target_id,
                    status=i.status,
                    image_ids=list(i.image_ids),
                )
                for i in result.items
            ],
        )


class EnrollmentBatchOut(BaseModel):
    batch_id: str
    state: EnrollmentStatus
    targets: list[TargetOut]

    @classmethod
    def of(cls, view: EnrollmentBatchView) -> "EnrollmentBatchOut":
        return cls(
            batch_id=view.batch_id,
            state=view.state,
            targets=[TargetOut.of(t) for t in view.targets],
        )


class BatchDeleteOut(BaseModel):
    deleted: int


# alerts


class BBoxOut(BaseModel):
    x: int
    y: int
    w: int
    h: int


class AlertOut(BaseModel):
    id: str
    owner_id: str
    stream_id: str
    watchlist_id: str
    target_id: str
    similarity: float
    calibrated_confidence: float | None
    bbox: BBoxOut
    track_id: int
    frame_ts: datetime
    created_at: datetime
    snapshot_url: str | None
    crop_url: str | None
    disposition: Disposition
    acknowledged: bool
    note: str | None

    @classmethod
    async def of(cls, view: AlertView, blob: BlobStore) -> "AlertOut":
        """``snapshot_url``/``crop_url`` are presigned here, per request — they expire,
        so a late consumer re-fetching ``GET /alerts/{id}`` always gets a fresh link
        rather than a stored one going stale."""
        return cls(
            id=view.id,
            owner_id=view.owner_id,
            stream_id=view.stream_id,
            watchlist_id=view.watchlist_id,
            target_id=view.target_id,
            similarity=view.similarity,
            calibrated_confidence=view.calibrated_confidence,
            bbox=BBoxOut(x=view.bbox.x, y=view.bbox.y, w=view.bbox.w, h=view.bbox.h),
            track_id=view.track_id,
            frame_ts=view.frame_ts,
            created_at=view.created_at,
            snapshot_url=await _presign(blob, view.evidence.snapshot_key),
            crop_url=await _presign(blob, view.evidence.crop_key),
            disposition=view.disposition,
            acknowledged=view.acknowledged,
            note=view.note,
        )


async def _presign(blob: BlobStore, key: str | None) -> str | None:
    """Best-effort: evidence that never made it to the blob store (a write failure,
    an expired/cleaned-up object) shouldn't take the whole alert lookup down with it."""
    if not key:
        return None
    try:
        return await blob.presigned_url(key)
    except NotFoundError:
        return None


class AlertPageOut(BaseModel):
    items: list[AlertOut]
    next_cursor: str | None

    @classmethod
    async def of(cls, page: AlertPage, blob: BlobStore) -> "AlertPageOut":
        items = [await AlertOut.of(a, blob) for a in page.items]
        return cls(items=items, next_cursor=page.next_cursor)


class ResolveIn(BaseModel):
    disposition: Disposition
    note: str | None = Field(default=None, max_length=2000)


# streams


class SourceIn(BaseModel):
    protocol: StreamProtocol
    url: str = Field(min_length=1, max_length=1024)
    username: str | None = None
    password: str | None = None
    transport: TransportProtocol = TransportProtocol.TCP


class SamplingIn(BaseModel):
    mode: SamplingMode = SamplingMode.ADAPTIVE
    target_fps: float = Field(default=10.0, gt=0, le=120)
    min_fps: float = Field(default=3.0, gt=0, le=120)
    motion_gating: bool = True


class RoiIn(BaseModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    w: float = Field(gt=0.0, le=1.0)
    h: float = Field(gt=0.0, le=1.0)


class StreamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    source: SourceIn
    camera_id: str | None = Field(default=None, max_length=128)
    watchlist_ids: list[str] = Field(default_factory=list)
    sampling: SamplingIn = Field(default_factory=SamplingIn)
    roi: list[RoiIn] = Field(default_factory=list)
    detect_classes: list[str] = Field(default_factory=list)


class StreamUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    source: SourceIn | None = None
    camera_id: str | None = Field(default=None, max_length=128)
    watchlist_ids: list[str] | None = None
    sampling: SamplingIn | None = None
    roi: list[RoiIn] | None = None
    detect_classes: list[str] | None = None
    enabled: bool | None = None


class StreamStateIn(BaseModel):
    running: bool


class PreviewOut(BaseModel):
    snapshot_url: str


class SamplingOut(BaseModel):
    mode: SamplingMode
    target_fps: float
    min_fps: float
    motion_gating: bool


class RoiOut(BaseModel):
    x: float
    y: float
    w: float
    h: float


class HealthOut(BaseModel):
    status: StreamStatus
    fps_in: float
    fps_processed: float
    frames_dropped_pct: float
    last_frame_at: datetime | None
    reconnect_count: int
    inference_p95_ms: float
    queue_depth: dict[str, int]
    last_error: str | None


class StreamOut(BaseModel):
    id: str
    owner_id: str
    name: str
    protocol: StreamProtocol
    url: str
    transport: TransportProtocol
    has_credentials: bool
    camera_id: str | None
    watchlist_ids: list[str]
    sampling: SamplingOut
    roi: list[RoiOut]
    detect_classes: list[str]
    enabled: bool
    desired_state: DesiredState
    live_status: StreamStatus
    health: HealthOut | None

    @classmethod
    def of(cls, view: StreamView) -> "StreamOut":
        return cls(
            id=view.id,
            owner_id=view.owner_id,
            name=view.name,
            protocol=view.protocol,
            url=view.url,
            transport=view.transport,
            has_credentials=view.has_credentials,
            camera_id=view.camera_id,
            watchlist_ids=list(view.watchlist_ids),
            sampling=SamplingOut(
                mode=view.sampling.mode,
                target_fps=view.sampling.target_fps,
                min_fps=view.sampling.min_fps,
                motion_gating=view.sampling.motion_gating,
            ),
            roi=[RoiOut(x=r.x, y=r.y, w=r.w, h=r.h) for r in view.roi],
            detect_classes=list(view.detect_classes),
            enabled=view.enabled,
            desired_state=view.desired_state,
            live_status=view.live_status,
            health=_health_out(view.health),
        )


def _health_out(health: StreamHealth | None) -> HealthOut | None:
    if health is None:
        return None
    return HealthOut(
        status=health.status,
        fps_in=health.fps_in,
        fps_processed=health.fps_processed,
        frames_dropped_pct=health.frames_dropped_pct,
        last_frame_at=health.last_frame_at,
        reconnect_count=health.reconnect_count,
        inference_p95_ms=health.inference_p95_ms,
        queue_depth=dict(health.queue_depth),
        last_error=health.last_error,
    )
