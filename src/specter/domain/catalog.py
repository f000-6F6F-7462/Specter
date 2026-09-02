"""Target-management aggregate: watchlists, targets, and their reference images."""

from dataclasses import dataclass, field
from enum import StrEnum

from specter.domain.quality import QualityReport, RejectionReason
from specter.platform.errors import RuleViolation


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

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise RuleViolation("target label must not be empty")

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
        if not self.name.strip():
            raise RuleViolation("watchlist name must not be empty")
        if not 0.0 <= self.match_threshold <= 1.0:
            raise RuleViolation(f"match_threshold out of range: {self.match_threshold}")
