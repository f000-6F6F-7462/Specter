import pytest

from specter.messaging.subjects import CameraEvent, build_all_cameras_subject, build_camera_subject


def test_camera_subject_contains_camera_id_and_event_when_built() -> None:
    subject = build_camera_subject("front_door", CameraEvent.MATCH_CONFIRMED)

    assert subject == "specter.cameras.front_door.match_confirmed"


def test_all_cameras_subject_uses_a_wildcard_for_the_camera_when_built() -> None:
    subject = build_all_cameras_subject(CameraEvent.STATUS_CHANGED)

    assert subject == "specter.cameras.*.status_changed"


@pytest.mark.parametrize("camera_id", ["", "front.door", "front door", "*", ">"])
def test_camera_subject_is_rejected_when_camera_id_is_not_a_single_token(camera_id: str) -> None:
    with pytest.raises(ValueError, match="cannot be used as a NATS subject token"):
        build_camera_subject(camera_id, CameraEvent.RULE_TRIGGERED)
