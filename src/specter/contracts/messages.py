"""Redis Stream message models — the cross-team wire contract.

Every model is frozen and rejects unknown
fields (``extra="forbid"``) so schema drift fails loudly on both sides.
"""

import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"


class _Wire(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class BBoxModel(_Wire):
    x: int
    y: int
    w: int = Field(gt=0)
    h: int = Field(gt=0)
    norm: bool = False


class StreamRef(_Wire):
    id: str
    name: str
    camera_id: str | None = None


class MatchInfo(_Wire):
    watchlist_id: str
    watchlist_name: str
    kind: str
    target_id: str
    target_label: str
    target_type: str
    similarity: float = Field(ge=0.0, le=1.0)
    threshold: float = Field(ge=0.0, le=1.0)
    calibrated_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    distance_metric: str = "cosine"


class DetectionInfo(_Wire):
    object_class: str = Field(alias="class")
    confidence: float = Field(ge=0.0, le=1.0)
    bbox: BBoxModel
    frame_ts: AwareDatetime
    frame_id: int = Field(ge=0)
    track_id: int = Field(ge=0)


class Evidence(_Wire):
    snapshot_url: str | None = None
    crop_url: str | None = None
    clip_url: str | None = None


class Dedup(_Wire):
    correlation_id: str
    hit_count: int = Field(ge=1)
    window_ms: int = Field(ge=0)
    first_seen_at: AwareDatetime


class MessageType(StrEnum):
    MATCH_EVENT = "match_event"
    STREAM_STATUS = "stream_status"
    ENROLLMENT_STATUS = "enrollment_status"
    ENROLL_JOB = "enroll_job"


class BrokerMessage(_Wire):
    """Envelope fields common to every message on every stream."""

    schema_version: str = SCHEMA_VERSION
    event_id: str
    occurred_at: AwareDatetime
    owner_id: str


class MatchEventMessage(BrokerMessage):
    type: Literal[MessageType.MATCH_EVENT] = MessageType.MATCH_EVENT
    stream: StreamRef
    match: MatchInfo
    detection: DetectionInfo
    evidence: Evidence = Field(default_factory=Evidence)
    dedup: Dedup


class StreamStatusMessage(BrokerMessage):
    type: Literal[MessageType.STREAM_STATUS] = MessageType.STREAM_STATUS
    stream_id: str
    status: str
    detail: str | None = None


class EnrollmentStatusMessage(BrokerMessage):
    type: Literal[MessageType.ENROLLMENT_STATUS] = MessageType.ENROLLMENT_STATUS
    batch_id: str
    target_id: str
    image_id: str | None = None
    status: str
    quality_score: float | None = Field(default=None, ge=0.0, le=1.0)
    rejection_reason: str | None = None


class EnrollJobMessage(BrokerMessage):
    type: Literal[MessageType.ENROLL_JOB] = MessageType.ENROLL_JOB
    batch_id: str
    target_id: str
    image_id: str
    blob_key: str
    modality: str


MESSAGE_MODELS: dict[str, type[BrokerMessage]] = {
    MessageType.MATCH_EVENT: MatchEventMessage,
    MessageType.STREAM_STATUS: StreamStatusMessage,
    MessageType.ENROLLMENT_STATUS: EnrollmentStatusMessage,
    MessageType.ENROLL_JOB: EnrollJobMessage,
}


def parse_message(raw: str | bytes | Mapping[str, object]) -> BrokerMessage:
    """Validate one stream payload into its concrete message model."""
    data = raw if isinstance(raw, Mapping) else json.loads(raw)
    if not isinstance(data, Mapping):
        raise ValueError("message payload must be a JSON object")
    tag = data.get("type")
    model = MESSAGE_MODELS.get(tag) if isinstance(tag, str) else None
    if model is None:
        raise ValueError(f"unknown message type: {tag!r}")
    return model.model_validate(data)
