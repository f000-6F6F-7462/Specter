import pytest

from specter.core.errors import InvalidEntityError
from specter.entities.cameras import (
    Camera,
    CameraCredentials,
    DesiredState,
    SamplingSettings,
)


def build_camera(*, is_enabled: bool = True) -> Camera:
    return Camera(
        id="camera_front_door",
        owner_id="owner_alice",
        name="Front door",
        source_url="rtsp://192.168.1.20/stream1",
        is_enabled=is_enabled,
    )


def test_camera_is_rejected_when_name_is_empty() -> None:
    with pytest.raises(InvalidEntityError, match="camera name"):
        Camera(id="camera_1", owner_id="owner_alice", name=" ", source_url="rtsp://camera")


def test_camera_is_rejected_when_watchlist_ids_repeat() -> None:
    with pytest.raises(InvalidEntityError, match="duplicates"):
        Camera(
            id="camera_1",
            owner_id="owner_alice",
            name="Lobby",
            source_url="rtsp://camera",
            watchlist_ids=("watchlist_a", "watchlist_a"),
        )


def test_start_returns_running_copy_when_camera_is_enabled() -> None:
    camera = build_camera()

    started_camera = camera.start()

    assert started_camera.should_run
    assert camera.desired_state is DesiredState.STOPPED


def test_start_is_rejected_when_camera_is_disabled() -> None:
    with pytest.raises(InvalidEntityError, match="disabled"):
        build_camera(is_enabled=False).start()


def test_disable_also_stops_camera_when_called_on_running_camera() -> None:
    disabled_camera = build_camera().start().disable()

    assert not disabled_camera.is_enabled
    assert disabled_camera.desired_state is DesiredState.STOPPED


def test_sampling_settings_are_rejected_when_minimum_exceeds_target() -> None:
    with pytest.raises(InvalidEntityError, match="minimum_fps"):
        SamplingSettings(target_fps=5.0, minimum_fps=8.0)


def test_password_is_hidden_when_credentials_are_printed() -> None:
    credentials = CameraCredentials(username="admin", password="very-secret")

    assert "very-secret" not in repr(credentials)
