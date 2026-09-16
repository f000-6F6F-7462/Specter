from dataclasses import replace

import pytest

from specter.core.errors import ConfigurationError, InvalidEntityError, NotFoundError
from specter.entities.cameras import Camera, CameraCredentials
from specter.entities.targets import TargetType
from specter.entities.watchlists import Watchlist
from specter.storage.cameras import (
    delete_camera,
    find_camera,
    find_camera_settings,
    list_cameras_to_run,
    list_owner_cameras,
    save_camera,
)
from specter.storage.credentials import CredentialCipher
from specter.storage.tables import CameraRecord
from specter.storage.watchlists import delete_watchlist, find_watchlist, save_watchlist

pytestmark = pytest.mark.usefixtures("database")

OWNER_ID = "owner_alice"


def build_watchlist(watchlist_id: str) -> Watchlist:
    return Watchlist(
        id=watchlist_id,
        owner_id=OWNER_ID,
        name=f"List {watchlist_id}",
        target_type=TargetType.PERSON,
        metadata={"site": "lobby"},
    )


def build_camera(camera_id: str, watchlist_ids: tuple[str, ...] = ()) -> Camera:
    return Camera(
        id=camera_id,
        owner_id=OWNER_ID,
        name=f"Camera {camera_id}",
        source_url="rtsp://192.168.1.20/stream1",
        credentials=CameraCredentials(username="admin", password="camera-password"),
        watchlist_ids=watchlist_ids,
        detection_classes=frozenset({"person", "car"}),
    )


def test_camera_is_unchanged_when_saved_and_loaded(cipher: CredentialCipher) -> None:
    save_watchlist(build_watchlist("watchlist_b"))
    save_watchlist(build_watchlist("watchlist_a"))
    camera = build_camera("camera_front_door", watchlist_ids=("watchlist_b", "watchlist_a"))

    save_camera(camera, cipher)

    assert find_camera("camera_front_door", cipher) == camera


def test_password_is_not_stored_in_plain_text_when_camera_is_saved(
    cipher: CredentialCipher,
) -> None:
    save_camera(build_camera("camera_front_door"), cipher)

    record = CameraRecord.get(CameraRecord.id == "camera_front_door")

    assert record.credentials_encrypted_password is not None
    assert "camera-password" not in record.credentials_encrypted_password


def test_saving_replaces_fields_when_camera_already_exists(cipher: CredentialCipher) -> None:
    save_camera(build_camera("camera_front_door"), cipher)

    save_camera(build_camera("camera_front_door").start(), cipher)

    camera = find_camera("camera_front_door", cipher)
    assert camera is not None
    assert camera.should_run
    assert len(list_owner_cameras(OWNER_ID, cipher)) == 1


def test_camera_is_rejected_when_it_uses_a_missing_watchlist(cipher: CredentialCipher) -> None:
    with pytest.raises(InvalidEntityError, match="watchlist that does not exist"):
        save_camera(build_camera("camera_front_door", ("watchlist_missing",)), cipher)


def test_only_running_enabled_cameras_are_listed_when_listing_cameras_to_run(
    cipher: CredentialCipher,
) -> None:
    save_camera(build_camera("camera_running").start(), cipher)
    save_camera(build_camera("camera_stopped"), cipher)
    save_camera(build_camera("camera_disabled").start().disable(), cipher)

    cameras_to_run = list_cameras_to_run(cipher)

    assert [camera.id for camera in cameras_to_run] == ["camera_running"]


def test_saving_credentials_fails_when_no_cipher_is_configured() -> None:
    with pytest.raises(ConfigurationError, match="credentials key"):
        save_camera(build_camera("camera_front_door"), None)


def test_camera_stops_using_watchlist_when_watchlist_is_deleted(cipher: CredentialCipher) -> None:
    save_watchlist(build_watchlist("watchlist_a"))
    save_camera(build_camera("camera_front_door", ("watchlist_a",)), cipher)

    delete_watchlist("watchlist_a")

    camera = find_camera("camera_front_door", cipher)
    assert camera is not None
    assert camera.watchlist_ids == ()


def test_watchlist_is_unchanged_when_saved_and_loaded() -> None:
    watchlist = build_watchlist("watchlist_a")

    save_watchlist(watchlist)

    assert find_watchlist("watchlist_a") == watchlist


def test_deleting_fails_when_camera_does_not_exist() -> None:
    with pytest.raises(NotFoundError):
        delete_camera("camera_missing")


def test_camera_settings_are_read_without_credentials_when_camera_has_them(
    cipher: CredentialCipher,
) -> None:
    save_camera(build_camera("camera_front_door"), cipher)

    camera_settings = find_camera_settings("camera_front_door")

    assert camera_settings == replace(build_camera("camera_front_door"), credentials=None)


def test_camera_settings_are_missing_when_camera_does_not_exist() -> None:
    assert find_camera_settings("camera_missing") is None
