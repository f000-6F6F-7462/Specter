"""NATS subject names used by Specter.

Events are published under their owner, so a consumer can follow a single owner's events, for
example with the subject ``specter.owners.owner_alice.>``.
"""

from enum import StrEnum

from specter.messaging.messages import (
    CameraStatusChangedMessage,
    ConfigurationChangedMessage,
    EnrollmentJobMessage,
    EnrollmentStatusChangedMessage,
    MatchConfirmedMessage,
    RuleTriggeredMessage,
    SpecterMessage,
)

ENROLLMENT_JOBS = "specter.enrollment.jobs"
SUBJECT_WILDCARD = "*"

# Characters that NATS gives a special meaning inside a subject token.
FORBIDDEN_TOKEN_CHARACTERS = frozenset(".*> \t\r\n")


class CameraEvent(StrEnum):
    """Events that a camera process publishes about its camera."""

    MATCH_CONFIRMED = "match_confirmed"
    RULE_TRIGGERED = "rule_triggered"
    STATUS_CHANGED = "status_changed"


class OwnerEvent(StrEnum):
    """Events that concern an owner rather than a single camera."""

    ENROLLMENT_STATUS_CHANGED = "enrollment.status_changed"
    CONFIGURATION_CHANGED = "configuration.changed"


def build_camera_subject(owner_id: str, camera_id: str, event: CameraEvent) -> str:
    """Returns the subject of an event from one of an owner's cameras.

    Raises:
        ValueError: An id is empty or contains a character reserved by NATS.
    """
    _require_subject_token(owner_id, "owner id")
    _require_subject_token(camera_id, "camera id")
    return f"specter.owners.{owner_id}.cameras.{camera_id}.{event}"


def build_all_cameras_subject(event: CameraEvent) -> str:
    """Returns a wildcard subject that matches an event from every camera of every owner."""
    return f"specter.owners.{SUBJECT_WILDCARD}.cameras.{SUBJECT_WILDCARD}.{event}"


def build_owner_subject(owner_id: str, event: OwnerEvent) -> str:
    """Returns the subject of an event that concerns one owner.

    Raises:
        ValueError: The owner id is empty or contains a character reserved by NATS.
    """
    _require_subject_token(owner_id, "owner id")
    return f"specter.owners.{owner_id}.{event}"


def build_all_owners_subject(event: OwnerEvent) -> str:
    """Returns a wildcard subject that matches an owner event of every owner."""
    return f"specter.owners.{SUBJECT_WILDCARD}.{event}"


def build_message_subject(message: SpecterMessage) -> str:
    """Returns the subject that the message is published on, derived from its type and ids.

    Raises:
        ValueError: An id cannot be used as a subject token, or the message type has no subject.
    """
    match message:
        case MatchConfirmedMessage():
            return build_camera_subject(
                message.owner_id, message.camera_id, CameraEvent.MATCH_CONFIRMED
            )
        case RuleTriggeredMessage():
            return build_camera_subject(
                message.owner_id, message.camera_id, CameraEvent.RULE_TRIGGERED
            )
        case CameraStatusChangedMessage():
            return build_camera_subject(
                message.owner_id, message.camera_id, CameraEvent.STATUS_CHANGED
            )
        case EnrollmentStatusChangedMessage():
            return build_owner_subject(message.owner_id, OwnerEvent.ENROLLMENT_STATUS_CHANGED)
        case ConfigurationChangedMessage():
            return build_owner_subject(message.owner_id, OwnerEvent.CONFIGURATION_CHANGED)
        case EnrollmentJobMessage():
            return ENROLLMENT_JOBS
    raise ValueError(f"{type(message).__name__} has no subject")


def _require_subject_token(value: str, description: str) -> None:
    if not value or not FORBIDDEN_TOKEN_CHARACTERS.isdisjoint(value):
        raise ValueError(f"{description} {value!r} cannot be used as a NATS subject token")
