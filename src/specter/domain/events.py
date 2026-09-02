"""In-process domain events.

These are what use cases emit and hand to the ``EventBus`` adapter for translation to
wire messages.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class DomainEvent:
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class EnrollmentRequested(DomainEvent):
    batch_id: str
    target_id: str
    image_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TargetEnrolled(DomainEvent):
    batch_id: str
    target_id: str
    image_id: str
    model_version: str
    quality_score: float | None = None


@dataclass(frozen=True, slots=True)
class ImageRejected(DomainEvent):
    batch_id: str
    target_id: str
    image_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class StreamStartRequested(DomainEvent):
    stream_id: str
    owner_id: str


@dataclass(frozen=True, slots=True)
class StreamStopRequested(DomainEvent):
    stream_id: str
    owner_id: str
