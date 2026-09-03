"""Pydantic request/response models for the REST API."""

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
from specter.domain.alerts import Disposition
from specter.domain.catalog import EnrollmentStatus, ImageStatus, TargetType, WatchlistKind


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# watchlists


class WatchlistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    type: TargetType
    kind: WatchlistKind = WatchlistKind.WATCHLIST
    match_threshold: float = Field(default=0.78, ge=0.0, le=1.0)


class WatchlistUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    match_threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class WatchlistOut(_Out):
    id: str
    owner_id: str
    name: str
    type: TargetType
    kind: WatchlistKind
    match_threshold: float
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
            quality=view.quality.__dict__ if view.quality else None,
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
    def of(cls, view: AlertView) -> "AlertOut":
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
            snapshot_url=view.evidence.snapshot_key,
            crop_url=view.evidence.crop_key,
            disposition=view.disposition,
            acknowledged=view.acknowledged,
            note=view.note,
        )


class AlertPageOut(BaseModel):
    items: list[AlertOut]
    next_cursor: str | None

    @classmethod
    def of(cls, page: AlertPage) -> "AlertPageOut":
        return cls(items=[AlertOut.of(a) for a in page.items], next_cursor=page.next_cursor)


class ResolveIn(BaseModel):
    disposition: Disposition
    note: str | None = Field(default=None, max_length=2000)
