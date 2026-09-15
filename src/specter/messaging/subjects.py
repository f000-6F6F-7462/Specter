"""NATS subject names used by Specter.

NATS exposes these subjects to MQTT clients with ``/`` in place of ``.``, so
``specter.cameras.front_door.match_confirmed`` is the MQTT topic
``specter/cameras/front_door/match_confirmed``.
"""

from enum import StrEnum

ENROLLMENT_JOBS = "specter.enrollment.jobs"
ENROLLMENT_STATUS_CHANGED = "specter.enrollment.status_changed"
CONFIGURATION_CHANGED = "specter.configuration.changed"

# Characters that NATS gives a special meaning inside a subject token.
FORBIDDEN_TOKEN_CHARACTERS = frozenset(".*> \t\r\n")


class CameraEvent(StrEnum):
    """Events that a camera process publishes about its camera."""

    MATCH_CONFIRMED = "match_confirmed"
    RULE_TRIGGERED = "rule_triggered"
    STATUS_CHANGED = "status_changed"


def build_camera_subject(camera_id: str, event: CameraEvent) -> str:
    """Returns the subject of an event from one camera.

    Raises:
        ValueError: The camera id is empty or contains a character reserved by NATS.
    """
    if not camera_id or not FORBIDDEN_TOKEN_CHARACTERS.isdisjoint(camera_id):
        raise ValueError(f"camera id {camera_id!r} cannot be used as a NATS subject token")
    return f"specter.cameras.{camera_id}.{event}"


def build_all_cameras_subject(event: CameraEvent) -> str:
    """Returns a wildcard subject that matches an event from every camera."""
    return f"specter.cameras.*.{event}"
