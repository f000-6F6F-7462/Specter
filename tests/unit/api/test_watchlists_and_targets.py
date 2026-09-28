import json
from pathlib import Path

from fastapi.testclient import TestClient
from httpx import Response

from specter.config.settings import Settings

OWNER_PATH = "/owners/owner_alice"
# The API checks only the type and size of an image; the detector decides whether it is usable.
JPEG_BYTES = b"\xff\xd8\xff\xe0 reference photo"


def create_watchlist(api_client: TestClient) -> dict[str, object]:
    response = api_client.post(
        f"{OWNER_PATH}/watchlists",
        json={"name": "Wanted", "target_type": "person", "kind": "blacklist"},
    )
    assert response.status_code == 201
    watchlist: dict[str, object] = response.json()
    return watchlist


def upload_targets(
    api_client: TestClient,
    watchlist_id: object,
    specifications: list[dict[str, object]],
    files: list[tuple[str, tuple[str, bytes, str]]],
) -> Response:
    response: Response = api_client.post(
        f"{OWNER_PATH}/watchlists/{watchlist_id}/targets",
        data={"targets": json.dumps(specifications)},
        files=files,
    )
    return response


def test_watchlist_thresholds_change_when_patched(api_client: TestClient) -> None:
    watchlist = create_watchlist(api_client)

    response = api_client.patch(
        f"{OWNER_PATH}/watchlists/{watchlist['id']}", json={"face_match_threshold_ratio": 0.5}
    )

    assert response.json()["face_match_threshold_ratio"] == 0.5
    assert response.json()["appearance_match_threshold_ratio"] == 0.75


def test_batch_creates_targets_with_pending_embeddings_and_stored_images(
    api_client: TestClient, api_settings: Settings
) -> None:
    watchlist = create_watchlist(api_client)

    response = upload_targets(
        api_client,
        watchlist["id"],
        [
            {"label": "Jane", "image_file_names": ["jane_front.jpg", "jane_back.jpg"]},
            {"label": "Bob", "image_file_names": ["bob.jpg"]},
        ],
        [
            ("images", ("jane_front.jpg", JPEG_BYTES, "image/jpeg")),
            ("images", ("jane_back.jpg", JPEG_BYTES, "image/jpeg")),
            ("images", ("bob.jpg", JPEG_BYTES, "image/jpeg")),
        ],
    )

    assert response.status_code == 201
    targets = response.json()
    assert [target["label"] for target in targets] == ["Jane", "Bob"]
    assert targets[0]["enrollment_batch_id"] == targets[1]["enrollment_batch_id"]
    assert targets[0]["enrollment_status"] == "queued"
    assert [
        (embedding["modality"], embedding["status"])
        for embedding in targets[0]["reference_images"][0]["embeddings"]
    ] == [("face", "pending"), ("appearance", "pending")]
    stored_images = list((api_settings.paths.data_directory / "reference_images").rglob("*.jpg"))
    assert len(stored_images) == 3
    assert all(image.read_bytes() == JPEG_BYTES for image in stored_images)


def test_reference_image_is_served_only_through_its_own_target(api_client: TestClient) -> None:
    watchlist = create_watchlist(api_client)
    target = upload_targets(
        api_client,
        watchlist["id"],
        [{"label": "Jane", "image_file_names": ["jane.jpg"]}],
        [("images", ("jane.jpg", JPEG_BYTES, "image/jpeg"))],
    ).json()[0]
    image_id = target["reference_images"][0]["id"]
    image_path = f"watchlists/{watchlist['id']}/targets/{target['id']}/images/{image_id}"

    own_image = api_client.get(f"{OWNER_PATH}/{image_path}")
    foreign_image = api_client.get(f"/owners/owner_bob/{image_path}")

    assert (own_image.status_code, own_image.content) == (200, JPEG_BYTES)
    assert own_image.headers["content-type"] == "image/jpeg"
    assert foreign_image.status_code == 404


def test_batch_is_rejected_when_a_named_file_was_not_uploaded(
    api_client: TestClient, api_settings: Settings
) -> None:
    watchlist = create_watchlist(api_client)

    response = upload_targets(
        api_client,
        watchlist["id"],
        [{"label": "Jane", "image_file_names": ["jane.jpg", "missing.jpg"]}],
        [("images", ("jane.jpg", JPEG_BYTES, "image/jpeg"))],
    )

    assert response.status_code == 422
    assert api_client.get(f"{OWNER_PATH}/watchlists/{watchlist['id']}/targets").json() == []
    assert not Path(api_settings.paths.data_directory / "reference_images").exists()


def test_batch_is_rejected_when_a_file_is_not_an_image(api_client: TestClient) -> None:
    watchlist = create_watchlist(api_client)

    response = upload_targets(
        api_client,
        watchlist["id"],
        [{"label": "Jane", "image_file_names": ["jane.gif"]}],
        [("images", ("jane.gif", b"GIF89a", "image/gif"))],
    )

    assert response.status_code == 422


def test_image_is_added_to_an_existing_target_when_uploaded(api_client: TestClient) -> None:
    watchlist = create_watchlist(api_client)
    target = upload_targets(api_client, watchlist["id"], [{"label": "Jane"}], []).json()[0]

    response = api_client.post(
        f"{OWNER_PATH}/watchlists/{watchlist['id']}/targets/{target['id']}/images",
        files=[("images", ("jane.png", b"\x89PNG photo", "image/png"))],
    )

    assert response.status_code == 201
    assert len(response.json()["reference_images"]) == 1


def test_enrollment_batch_of_another_owner_is_not_found(api_client: TestClient) -> None:
    watchlist = create_watchlist(api_client)
    target = upload_targets(api_client, watchlist["id"], [{"label": "Jane"}], []).json()[0]

    own_batch = api_client.get(f"{OWNER_PATH}/enrollment-batches/{target['enrollment_batch_id']}")
    foreign_batch = api_client.get(
        f"/owners/owner_bob/enrollment-batches/{target['enrollment_batch_id']}"
    )

    assert (own_batch.status_code, foreign_batch.status_code) == (200, 404)
