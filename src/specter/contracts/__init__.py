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

__all__ = [
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
