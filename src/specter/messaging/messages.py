"""Messages and shared values that Specter writes to NATS, which are its contract with clients.

The major part of ``schema_version`` changes only when a change breaks existing consumers.
"""

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from specter.core.identifiers import new_identifier
from specter.entities.cameras import CameraStatus
from specter.entities.rules import CrossingDirection, RuleKind
from specter.entities.targets import EmbeddingModality, ImageStatus, RejectionReason

SCHEMA_VERSION = "1.0"


class EntityKind(StrEnum):
    """Which kind of entity changed."""

    CAMERA = "camera"
    ZONE = "zone"
    RULE = "rule"
    WATCHLIST = "watchlist"
    TARGET = "target"


class ChangeKind(StrEnum):
    """How an entity changed."""

    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"


def new_message_id() -> str:
    """Returns a new random message id."""
    return new_identifier("message")


class _MessagePart(BaseModel):
    # Unknown fields are rejected so producers and consumers notice a schema mismatch at once.
    model_config = ConfigDict(frozen=True, extra="forbid")


class MessageBoundingBox(_MessagePart):
    """A bounding box as fractions of the frame's width and height."""

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)


class SpecterMessage(_MessagePart):
    """Fields that every Specter message carries."""

    schema_version: str = SCHEMA_VERSION
    message_id: str = Field(default_factory=new_message_id)
    occurred_at: AwareDatetime
    owner_id: str


class MatchConfirmedMessage(SpecterMessage):
    """A camera's track was confirmed as a watchlist target."""

    camera_id: str
    track_id: int = Field(ge=0)
    watchlist_id: str
    target_id: str
    modality: EmbeddingModality
    similarity_ratio: float = Field(ge=0.0, le=1.0)
    margin_ratio: float = Field(ge=0.0, le=1.0)
    threshold_ratio: float = Field(ge=0.0, le=1.0)
    object_class: str
    bounding_box: MessageBoundingBox
    first_seen_at: AwareDatetime
    frame_captured_at: AwareDatetime
    # Relative to the data directory; None when the snapshot could not be written.
    snapshot_path: str | None = None


class RuleTriggeredMessage(SpecterMessage):
    """A rule fired for a camera's track."""

    camera_id: str
    rule_id: str
    rule_kind: RuleKind
    zone_id: str | None = None
    track_id: int = Field(ge=0)
    object_class: str
    bounding_box: MessageBoundingBox
    dwell_seconds: float | None = Field(default=None, ge=0.0)
    crossing_direction: CrossingDirection | None = None
    frame_captured_at: AwareDatetime
    # Relative to the data directory; None when the snapshot could not be written.
    snapshot_path: str | None = None


class CameraStatusChangedMessage(SpecterMessage):
    """A camera process changed its live status."""

    camera_id: str
    status: CameraStatus
    detail: str | None = None


class EnrollmentJobMessage(SpecterMessage):
    """A reference image is waiting to be embedded by the detector."""

    target_id: str
    reference_image_id: str
    image_path: str
    modality: EmbeddingModality


class EnrollmentStatusChangedMessage(SpecterMessage):
    """A reference image was embedded or rejected."""

    target_id: str
    reference_image_id: str
    status: ImageStatus
    rejection_reason: RejectionReason | None = None
    quality_score_ratio: float | None = Field(default=None, ge=0.0, le=1.0)


class ConfigurationChangedMessage(SpecterMessage):
    """A camera, zone, rule, watchlist or target was created, updated or deleted."""

    entity_kind: EntityKind
    entity_id: str
    change_kind: ChangeKind


MESSAGE_TYPES: tuple[type[SpecterMessage], ...] = (
    MatchConfirmedMessage,
    RuleTriggeredMessage,
    CameraStatusChangedMessage,
    EnrollmentJobMessage,
    EnrollmentStatusChangedMessage,
    ConfigurationChangedMessage,
)


class CameraHealthReport(_MessagePart):
    """A camera process's latest health, which it keeps refreshing in the camera health bucket."""

    owner_id: str
    camera_id: str
    status: CameraStatus
    reported_at: AwareDatetime


# Every model that clients read from NATS, as a message or as a key-value bucket value.
CONTRACT_MODEL_TYPES: tuple[type[BaseModel], ...] = (*MESSAGE_TYPES, CameraHealthReport)
