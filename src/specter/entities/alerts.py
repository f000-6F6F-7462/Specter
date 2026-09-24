"""Alerts raised by identity matches and rules, and how people review them."""

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum

from specter.core.errors import InvalidEntityError
from specter.entities.geometry import NormalizedBoundingBox
from specter.entities.rules import CrossingDirection, RuleKind
from specter.entities.targets import EmbeddingModality
from specter.entities.validation import require_non_negative, require_ratio


class Disposition(StrEnum):
    """A reviewer's verdict on an alert."""

    UNREVIEWED = "unreviewed"
    TRUE_POSITIVE = "true_positive"
    FALSE_POSITIVE = "false_positive"


class AlertKind(StrEnum):
    """What raised an alert."""

    IDENTITY_MATCH = "identity_match"
    RULE = "rule"


@dataclass(frozen=True, slots=True)
class AlertReview:
    """How a person has reviewed an alert so far."""

    disposition: Disposition = Disposition.UNREVIEWED
    is_acknowledged: bool = False
    note: str | None = None

    def acknowledge(self) -> "AlertReview":
        """Returns an acknowledged copy."""
        return replace(self, is_acknowledged=True)

    def resolve(self, disposition: Disposition, note: str | None = None) -> "AlertReview":
        """Returns a copy with the reviewer's verdict, which also acknowledges the alert.

        Raises:
            InvalidEntityError: The verdict is ``unreviewed``.
        """
        if disposition is Disposition.UNREVIEWED:
            raise InvalidEntityError("an alert cannot be resolved as unreviewed")
        return replace(self, disposition=disposition, is_acknowledged=True, note=note)


@dataclass(frozen=True, slots=True)
class IdentityMatchAlert:
    """A track confirmed as a watchlist target."""

    id: str
    owner_id: str
    camera_id: str
    track_id: int
    watchlist_id: str
    target_id: str
    # A face match is much stronger evidence than an appearance match, which clothing can fool.
    modality: EmbeddingModality
    similarity_ratio: float
    margin_ratio: float
    object_class: str
    bounding_box: NormalizedBoundingBox
    frame_captured_at: datetime
    created_at: datetime
    # Relative to the data directory; None when evidence capture is turned off or failed.
    snapshot_path: str | None = None
    review: AlertReview = field(default_factory=AlertReview)

    def __post_init__(self) -> None:
        require_ratio(self.similarity_ratio, "similarity_ratio")
        require_ratio(self.margin_ratio, "margin_ratio")


@dataclass(frozen=True, slots=True)
class RuleAlert:
    """A rule that fired for a track."""

    id: str
    owner_id: str
    camera_id: str
    track_id: int
    rule_id: str
    rule_kind: RuleKind
    zone_id: str | None
    object_class: str
    bounding_box: NormalizedBoundingBox
    dwell_seconds: float | None
    crossing_direction: CrossingDirection | None
    frame_captured_at: datetime
    created_at: datetime
    # Relative to the data directory; None when evidence capture is turned off or failed.
    snapshot_path: str | None = None
    review: AlertReview = field(default_factory=AlertReview)

    def __post_init__(self) -> None:
        if self.rule_kind is RuleKind.ZONE_OCCUPANCY and self.zone_id is None:
            raise InvalidEntityError("a zone occupancy alert needs a zone_id")
        if self.rule_kind is RuleKind.LINE_CROSSING and self.crossing_direction is None:
            raise InvalidEntityError("a line crossing alert needs a crossing_direction")
        if self.dwell_seconds is not None:
            require_non_negative(self.dwell_seconds, "dwell_seconds")


type Alert = IdentityMatchAlert | RuleAlert
