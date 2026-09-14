"""Request and view shapes for the alerts use cases."""

from dataclasses import dataclass
from datetime import datetime

from specter.domain.alerts import Alert, Disposition, MatchEvidence
from specter.domain.vision import BBox


@dataclass(frozen=True, slots=True)
class AlertFilter:
    stream_id: str | None = None
    watchlist_id: str | None = None
    disposition: Disposition | None = None
    min_confidence: float | None = None
    since: datetime | None = None
    until: datetime | None = None
    limit: int = 50
    cursor: str | None = None


@dataclass(frozen=True, slots=True)
class AlertView:
    id: str
    owner_id: str
    stream_id: str
    watchlist_id: str
    target_id: str
    similarity: float
    calibrated_confidence: float | None
    bbox: BBox
    track_id: int
    frame_ts: datetime
    created_at: datetime
    evidence: MatchEvidence
    disposition: Disposition
    acknowledged: bool
    note: str | None

    @classmethod
    def of(cls, alert: Alert) -> "AlertView":
        return cls(
            id=alert.id,
            owner_id=alert.owner_id,
            stream_id=alert.stream_id,
            watchlist_id=alert.watchlist_id,
            target_id=alert.target_id,
            similarity=alert.similarity,
            calibrated_confidence=alert.calibrated_confidence,
            bbox=alert.bbox,
            track_id=alert.track_id,
            frame_ts=alert.frame_ts,
            created_at=alert.created_at,
            evidence=alert.evidence,
            disposition=alert.disposition,
            acknowledged=alert.acknowledged,
            note=alert.note,
        )


@dataclass(frozen=True, slots=True)
class AlertPage:
    items: tuple[AlertView, ...]
    next_cursor: str | None
