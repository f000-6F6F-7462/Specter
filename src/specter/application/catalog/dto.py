"""Request and view objects for the catalog use cases.

These are the catalog context's public data shapes. The HTTP layer maps its Pydantic
request bodies to these and these to its response bodies;
"""

from dataclasses import dataclass, field

from specter.domain.catalog import (
    EnrollmentStatus,
    ImageStatus,
    ReferenceImage,
    Target,
    TargetType,
    Watchlist,
    WatchlistKind,
)
from specter.domain.quality import QualityReport, RejectionReason

# watchlists


@dataclass(frozen=True, slots=True)
class CreateWatchlistRequest:
    owner_id: str
    name: str
    type: TargetType
    kind: WatchlistKind = WatchlistKind.WATCHLIST
    match_threshold: float = 0.78


@dataclass(frozen=True, slots=True)
class UpdateWatchlistRequest:
    name: str | None = None
    match_threshold: float | None = None


@dataclass(frozen=True, slots=True)
class WatchlistView:
    id: str
    owner_id: str
    name: str
    type: TargetType
    kind: WatchlistKind
    match_threshold: float
    target_count: int

    @classmethod
    def of(cls, wl: Watchlist, *, target_count: int) -> "WatchlistView":
        return cls(
            id=wl.id,
            owner_id=wl.owner_id,
            name=wl.name,
            type=wl.type,
            kind=wl.kind,
            match_threshold=wl.match_threshold,
            target_count=target_count,
        )


#  targets


@dataclass(frozen=True, slots=True)
class UploadedImage:
    name: str
    data: bytes
    content_type: str


@dataclass(frozen=True, slots=True)
class TargetSpec:
    ref: str
    label: str
    type: TargetType
    image_names: tuple[str, ...]
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EnrollTargetsRequest:
    owner_id: str
    watchlist_id: str
    targets: tuple[TargetSpec, ...]
    images: tuple[UploadedImage, ...]


@dataclass(frozen=True, slots=True)
class EnrolledTargetView:
    ref: str
    target_id: str
    status: EnrollmentStatus
    image_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EnrollTargetsResult:
    batch_id: str
    items: tuple[EnrolledTargetView, ...]


@dataclass(frozen=True, slots=True)
class AddImagesRequest:
    owner_id: str
    target_id: str
    images: tuple[UploadedImage, ...]


@dataclass(frozen=True, slots=True)
class UpdateTargetRequest:
    label: str | None = None
    enabled: bool | None = None
    metadata: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class ReferenceImageView:
    id: str
    blob_key: str
    status: ImageStatus
    quality: QualityReport | None
    rejection_reason: RejectionReason | None
    model_version: str | None

    @classmethod
    def of(cls, img: ReferenceImage) -> "ReferenceImageView":
        return cls(
            id=img.id,
            blob_key=img.blob_key,
            status=img.status,
            quality=img.quality,
            rejection_reason=img.rejection_reason,
            model_version=img.model_version,
        )


@dataclass(frozen=True, slots=True)
class TargetView:
    id: str
    watchlist_id: str
    label: str
    type: TargetType
    enabled: bool
    status: EnrollmentStatus
    batch_id: str | None
    metadata: dict[str, object]
    images: tuple[ReferenceImageView, ...]

    @classmethod
    def of(cls, target: Target) -> "TargetView":
        return cls(
            id=target.id,
            watchlist_id=target.watchlist_id,
            label=target.label,
            type=target.type,
            enabled=target.enabled,
            status=target.status,
            batch_id=target.batch_id,
            metadata=dict(target.metadata),
            images=tuple(ReferenceImageView.of(i) for i in target.images),
        )


@dataclass(frozen=True, slots=True)
class EnrollmentBatchView:
    batch_id: str
    state: EnrollmentStatus
    targets: tuple[TargetView, ...]
