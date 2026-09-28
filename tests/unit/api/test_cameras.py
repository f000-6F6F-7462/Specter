from fastapi.testclient import TestClient

OWNER_PATH = "/owners/owner_alice"
CAMERA_BODY = {
    "name": "Front door",
    "source_url": "rtsp://192.168.1.20/stream",
    "credentials": {"username": "admin", "password": "secret"},
}


def create_camera(api_client: TestClient) -> dict[str, object]:
    response = api_client.post(f"{OWNER_PATH}/cameras", json=CAMERA_BODY)
    assert response.status_code == 201
    camera: dict[str, object] = response.json()
    return camera


def test_created_camera_hides_its_password(api_client: TestClient) -> None:
    camera = create_camera(api_client)

    assert camera["username"] == "admin"
    assert camera["has_password"] is True
    assert "secret" not in str(camera)
    assert camera["desired_state"] == "stopped"
    assert camera["live_status"] is None


def test_camera_of_another_owner_is_not_found(api_client: TestClient) -> None:
    camera = create_camera(api_client)

    response = api_client.get(f"/owners/owner_bob/cameras/{camera['id']}")

    assert response.status_code == 404


def test_update_keeps_the_password_when_credentials_are_omitted(api_client: TestClient) -> None:
    camera = create_camera(api_client)

    renamed = api_client.patch(f"{OWNER_PATH}/cameras/{camera['id']}", json={"name": "Gate"})
    cleared = api_client.patch(f"{OWNER_PATH}/cameras/{camera['id']}", json={"credentials": None})

    assert (renamed.json()["name"], renamed.json()["has_password"]) == ("Gate", True)
    assert (cleared.json()["username"], cleared.json()["has_password"]) == (None, False)


def test_update_replaces_metadata_and_keeps_other_fields(api_client: TestClient) -> None:
    camera = create_camera(api_client)

    response = api_client.patch(
        f"{OWNER_PATH}/cameras/{camera['id']}", json={"metadata": {"location": "Gate"}}
    )

    assert response.json()["metadata"] == {"location": "Gate"}
    assert response.json()["name"] == camera["name"]


def test_camera_runs_only_between_start_and_stop(api_client: TestClient) -> None:
    camera = create_camera(api_client)

    started = api_client.post(f"{OWNER_PATH}/cameras/{camera['id']}/start")
    stopped = api_client.post(f"{OWNER_PATH}/cameras/{camera['id']}/stop")

    assert started.json()["desired_state"] == "running"
    assert stopped.json()["desired_state"] == "stopped"


def test_disabled_camera_cannot_be_started(api_client: TestClient) -> None:
    camera = create_camera(api_client)
    api_client.patch(f"{OWNER_PATH}/cameras/{camera['id']}", json={"is_enabled": False})

    response = api_client.post(f"{OWNER_PATH}/cameras/{camera['id']}/start")

    assert response.status_code == 422


def test_camera_is_rejected_when_its_url_contains_a_password(api_client: TestClient) -> None:
    response = api_client.post(
        f"{OWNER_PATH}/cameras",
        json={"name": "Gate", "source_url": "rtsp://admin:secret@192.168.1.21/stream"},
    )

    assert response.status_code == 422
    assert "secret" not in response.text


def test_camera_is_rejected_when_it_uses_another_owners_watchlist(api_client: TestClient) -> None:
    watchlist = api_client.post(
        "/owners/owner_bob/watchlists", json={"name": "Wanted", "target_type": "person"}
    ).json()

    response = api_client.post(
        f"{OWNER_PATH}/cameras", json={**CAMERA_BODY, "watchlist_ids": [watchlist["id"]]}
    )

    assert response.status_code == 404


def test_deleted_camera_is_not_found(api_client: TestClient) -> None:
    camera = create_camera(api_client)

    deleted = api_client.delete(f"{OWNER_PATH}/cameras/{camera['id']}")
    read_after_delete = api_client.get(f"{OWNER_PATH}/cameras/{camera['id']}")

    assert (deleted.status_code, read_after_delete.status_code) == (204, 404)


def test_live_video_is_refused_when_camera_is_not_running(api_client: TestClient) -> None:
    camera = create_camera(api_client)

    response = api_client.get(f"{OWNER_PATH}/cameras/{camera['id']}/live/frame.jpeg")

    assert response.status_code == 409
