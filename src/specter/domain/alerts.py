"""Alert aggregate and the rich in-process ``MatchEvent``.

``MatchEvent`` is the domain object the pipeline's emit stage builds. The application
layer maps it to ``specter.contracts.MatchEventMessage`` (adding presigned URLs) before
it hits the broker — the domain never imports the wire contract.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from specter.core.errors import RuleViolation
from specter.domain.vision import BBox


class Disposition(StrEnum):
    UNREVIEWED = "unreviewed"
    TRUE_POSITIVE = "true_positive"
    FALSE_POSITIVE = "false_positive"


@dataclass(frozen=True, slots=True)
class MatchEvidence:
    snapshot_key: str | None = None
    crop_key: str | None = None
    clip_key: str | None = None


@dataclass(frozen=True, slots=True)
class MatchEvent:
    event_id: str
    owner_id: str
    occurred_at: datetime
    stream_id: str
    stream_name: str
    camera_id: str | None
    watchlist_id: str
    watchlist_name: str
    watchlist_kind: str
    target_id: str
    target_label: str
    target_type: str
    similarity: float
    threshold: float
    detection_class: str
    detection_confidence: float
    bbox: BBox
    frame_ts: datetime
    frame_id: int
    track_id: int
    correlation_id: str
    hit_count: int
    window_ms: int
    first_seen_at: datetime
    calibrated_confidence: float | None = None
    evidence: MatchEvidence = MatchEvidence()


@dataclass(slots=True)
class Alert:
    id: str
    owner_id: str
    stream_id: str
    watchlist_id: str
    target_id: str
    similarity: float
    bbox: BBox
    track_id: int
    frame_ts: datetime
    created_at: datetime
    calibrated_confidence: float | None = None
    evidence: MatchEvidence = MatchEvidence()
    disposition: Disposition = Disposition.UNREVIEWED
    acknowledged: bool = False
    note: str | None = None

    def acknowledge(self) -> None:
        self.acknowledged = True

    def resolve(self, disposition: Disposition, note: str | None = None) -> None:
        if disposition is Disposition.UNREVIEWED:
            raise RuleViolation("cannot resolve an alert back to 'unreviewed'")
        self.disposition = disposition
        self.acknowledged = True
        self.note = note

    @classmethod
    def from_event(cls, event: MatchEvent) -> "Alert":
        return cls(
            id=event.event_id,
            owner_id=event.owner_id,
            stream_id=event.stream_id,
            watchlist_id=event.watchlist_id,
            target_id=event.target_id,
            similarity=event.similarity,
            bbox=event.bbox,
            track_id=event.track_id,
            frame_ts=event.frame_ts,
            created_at=event.occurred_at,
            calibrated_confidence=event.calibrated_confidence,
            evidence=event.evidence,
        )
