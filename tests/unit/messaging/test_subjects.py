from datetime import UTC, datetime

import pytest

from specter.entities.cameras import CameraStatus
from specter.entities.targets import EmbeddingModality, ImageStatus
from specter.messaging.messages import (
    CameraStatusChangedMessage,
    ChangeKind,
    ConfigurationChangedMessage,
    EnrollmentJobMessage,
    EnrollmentStatusChangedMessage,
    EntityKind,
    SpecterMessage,
)
from specter.messaging.subjects import (
    ENROLLMENT_JOBS,
    CameraEvent,
    OwnerEvent,
    build_all_cameras_subject,
    build_all_owners_subject,
    build_camera_subject,
    build_message_subject,
    build_owner_subject,
)

OCCURRED_AT = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
OWNER_ID = "owner_alice"


def test_camera_subject_contains_owner_camera_and_event_when_built() -> None:
    subject = build_camera_subject("owner_alice", "front_door", CameraEvent.MATCH_CONFIRMED)

    assert subject == "specter.owners.owner_alice.cameras.front_door.match_confirmed"


def test_all_cameras_subject_uses_wildcards_for_owner_and_camera_when_built() -> None:
    subject = build_all_cameras_subject(CameraEvent.STATUS_CHANGED)

    assert subject == "specter.owners.*.cameras.*.status_changed"


def test_owner_subject_contains_owner_and_event_when_built() -> None:
    subject = build_owner_subject("owner_alice", OwnerEvent.CONFIGURATION_CHANGED)

    assert subject == "specter.owners.owner_alice.configuration.changed"


def test_all_owners_subject_uses_a_wildcard_for_owner_when_built() -> None:
    subject = build_all_owners_subject(OwnerEvent.ENROLLMENT_STATUS_CHANGED)

    assert subject == "specter.owners.*.enrollment.status_changed"


@pytest.mark.parametrize("unsafe_id", ["", "front.door", "front door", "*", ">"])
def test_camera_subject_is_rejected_when_camera_id_is_not_a_single_token(unsafe_id: str) -> None:
    with pytest.raises(ValueError, match="cannot be used as a NATS subject token"):
        build_camera_subject("owner_alice", unsafe_id, CameraEvent.RULE_TRIGGERED)


@pytest.mark.parametrize("unsafe_id", ["", "alice.smith", "*"])
def test_owner_subject_is_rejected_when_owner_id_is_not_a_single_token(unsafe_id: str) -> None:
    with pytest.raises(ValueError, match="cannot be used as a NATS subject token"):
        build_owner_subject(unsafe_id, OwnerEvent.CONFIGURATION_CHANGED)


@pytest.mark.parametrize(
    ("message", "expected_subject"),
    [
        (
            CameraStatusChangedMessage(
                occurred_at=OCCURRED_AT,
                owner_id=OWNER_ID,
                camera_id="front_door",
                status=CameraStatus.RUNNING,
            ),
            "specter.owners.owner_alice.cameras.front_door.status_changed",
        ),
        (
            EnrollmentStatusChangedMessage(
                occurred_at=OCCURRED_AT,
                owner_id=OWNER_ID,
                target_id="target_jane",
                reference_image_id="image_1",
                status=ImageStatus.EMBEDDED,
            ),
            "specter.owners.owner_alice.enrollment.status_changed",
        ),
        (
            ConfigurationChangedMessage(
                occurred_at=OCCURRED_AT,
                owner_id=OWNER_ID,
                entity_kind=EntityKind.CAMERA,
                entity_id="front_door",
                change_kind=ChangeKind.UPDATED,
            ),
            "specter.owners.owner_alice.configuration.changed",
        ),
        (
            EnrollmentJobMessage(
                occurred_at=OCCURRED_AT,
                owner_id=OWNER_ID,
                target_id="target_jane",
                reference_image_id="image_1",
                image_path="reference_images/image_1.jpg",
                modality=EmbeddingModality.FACE,
            ),
            ENROLLMENT_JOBS,
        ),
    ],
)
def test_message_subject_follows_message_type_and_ids_when_built(
    message: SpecterMessage, expected_subject: str
) -> None:
    assert build_message_subject(message) == expected_subject


def test_message_subject_is_rejected_when_message_type_has_no_subject() -> None:
    message = SpecterMessage(occurred_at=OCCURRED_AT, owner_id=OWNER_ID)

    with pytest.raises(ValueError, match="has no subject"):
        build_message_subject(message)
