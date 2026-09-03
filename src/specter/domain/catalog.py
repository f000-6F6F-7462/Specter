"""Target-management aggregate: watchlists, targets, and their reference images."""

from dataclasses import dataclass, field
from enum import StrEnum

from specter.core.errors import RuleViolation
from specter.domain.quality import QualityReport, RejectionReason


def _require_text(value: str, field_name: str) -> str:
    if not value.strip():
        raise RuleViolation(f"{field_name} must not be empty")
    return value


def _require_unit_interval(value: float, field_name: str) -> float:
    if not 0.0 <= value <= 1.0:
        raise RuleViolation(f"{field_name} out of range: {value}")
    return value


class TargetType(StrEnum):
    PERSON = "person"
    VEHICLE = "vehicle"
    OBJECT = "object"


class WatchlistKind(StrEnum):
    WATCHLIST = "watchlist"
    BLACKLIST = "blacklist"


class ImageStatus(StrEnum):
    PENDING = "pending"
    EMBEDDED = "embedded"
    REJECTED = "rejected"


class EnrollmentStatus(StrEnum):
    QUEUED = "queued"
    PARTIAL = "partial"
    READY = "ready"
    FAILED = "failed"


@dataclass(slots=True)
class ReferenceImage:
    id: str
    blob_key: str
    status: ImageStatus = ImageStatus.PENDING
    quality: QualityReport | None = None
    rejection_reason: RejectionReason | None = None
    model_version: str | None = None

    def mark_embedded(self, model_version: str) -> None:
        self.status = ImageStatus.EMBEDDED
        self.model_version = model_version
        self.rejection_reason = None

    def mark_rejected(self, reason: RejectionReason) -> None:
        self.status = ImageStatus.REJECTED
        self.rejection_reason = reason


@dataclass(slots=True)
class Target:
    id: str
    watchlist_id: str
    label: str
    type: TargetType
    images: list[ReferenceImage] = field(default_factory=list)
    enabled: bool = True
    metadata: dict[str, object] = field(default_factory=dict)
    batch_id: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.label, "target label")

    def rename(self, label: str) -> None:
        self.label = _require_text(label, "target label")

    def image(self, image_id: str) -> ReferenceImage | None:
        return next((i for i in self.images if i.id == image_id), None)

    def remove_image(self, image_id: str) -> bool:
        before = len(self.images)
        self.images = [i for i in self.images if i.id != image_id]
        return len(self.images) != before

    @property
    def status(self) -> EnrollmentStatus:
        if not self.images:
            return EnrollmentStatus.QUEUED
        embedded = [i for i in self.images if i.status is ImageStatus.EMBEDDED]
        if not embedded and all(i.status is ImageStatus.REJECTED for i in self.images):
            return EnrollmentStatus.FAILED
        if embedded and len(embedded) < len(self.images):
            return EnrollmentStatus.PARTIAL
        return EnrollmentStatus.READY if embedded else EnrollmentStatus.QUEUED

    @property
    def embedded_images(self) -> list[ReferenceImage]:
        return [i for i in self.images if i.status is ImageStatus.EMBEDDED]


@dataclass(slots=True)
class Watchlist:
    id: str
    owner_id: str
    name: str
    type: TargetType
    kind: WatchlistKind = WatchlistKind.WATCHLIST
    match_threshold: float = 0.78

    def __post_init__(self) -> None:
        _require_text(self.name, "watchlist name")
        _require_unit_interval(self.match_threshold, "match_threshold")

    def rename(self, name: str) -> None:
        self.name = _require_text(name, "watchlist name")

    def set_threshold(self, value: float) -> None:
        self.match_threshold = _require_unit_interval(value, "match_threshold")
