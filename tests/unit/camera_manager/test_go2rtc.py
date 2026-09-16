from specter.camera_manager.go2rtc import build_camera_source_url
from specter.entities.cameras import Camera, CameraCredentials


def build_camera(source_url: str, credentials: CameraCredentials | None = None) -> Camera:
    return Camera(
        id="camera_front_door",
        owner_id="owner_alice",
        name="Front door",
        source_url=source_url,
        credentials=credentials,
    )


def test_source_url_is_unchanged_when_camera_has_no_credentials() -> None:
    camera = build_camera("rtsp://192.168.1.20:554/stream1")

    assert build_camera_source_url(camera) == "rtsp://192.168.1.20:554/stream1"


def test_credentials_are_encoded_into_source_url_when_they_contain_special_characters() -> None:
    camera = build_camera(
        "rtsp://192.168.1.20:554/stream1",
        CameraCredentials(username="admin", password="p@ss:w/rd"),
    )

    assert (
        build_camera_source_url(camera) == "rtsp://admin:p%40ss%3Aw%2Frd@192.168.1.20:554/stream1"
    )


def test_user_in_source_url_is_replaced_when_camera_has_its_own_credentials() -> None:
    camera = build_camera(
        "rtsp://old@192.168.1.20/stream1",
        CameraCredentials(username="admin", password="new"),
    )

    assert build_camera_source_url(camera) == "rtsp://admin:new@192.168.1.20/stream1"
