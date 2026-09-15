import pytest

from specter.messaging.subjects import (
    CameraEvent,
    OwnerEvent,
    build_all_cameras_subject,
    build_all_owners_subject,
    build_camera_subject,
    build_owner_subject,
)


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
