"""Wire contracts for the Redis Streams."""

from specter.contracts.messages import (
    MESSAGE_MODELS,
    SCHEMA_VERSION,
    BBoxModel,
    BrokerMessage,
    Dedup,
    DetectionInfo,
    EnrollJobMessage,
    EnrollmentStatusMessage,
    Evidence,
    MatchEventMessage,
    MatchInfo,
    MessageType,
    StreamRef,
    StreamStatusMessage,
    parse_message,
)
from specter.contracts.streams import (
    ENROLL_GROUP,
    EVENTS_ENROLLMENT,
    EVENTS_MATCH,
    EVENTS_STREAM_STATUS,
    JOBS_ENROLL,
)

__all__ = [
    "ENROLL_GROUP",
    "EVENTS_ENROLLMENT",
    "EVENTS_MATCH",
    "EVENTS_STREAM_STATUS",
    "JOBS_ENROLL",
    "MESSAGE_MODELS",
    "SCHEMA_VERSION",
    "BBoxModel",
    "BrokerMessage",
    "Dedup",
    "DetectionInfo",
    "EnrollJobMessage",
    "EnrollmentStatusMessage",
    "Evidence",
    "MatchEventMessage",
    "MatchInfo",
    "MessageType",
    "StreamRef",
    "StreamStatusMessage",
    "parse_message",
]
