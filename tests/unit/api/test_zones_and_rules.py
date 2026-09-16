from fastapi.testclient import TestClient

OWNER_PATH = "/owners/owner_alice"
SQUARE = [{"x": 0.1, "y": 0.1}, {"x": 0.5, "y": 0.1}, {"x": 0.5, "y": 0.5}, {"x": 0.1, "y": 0.5}]


def create_camera_path(api_client: TestClient) -> str:
    camera = api_client.post(
        f"{OWNER_PATH}/cameras", json={"name": "Lobby", "source_url": "rtsp://10.0.0.5/stream"}
    ).json()
    return f"{OWNER_PATH}/cameras/{camera['id']}"


def test_zone_rule_watches_a_zone_of_the_same_camera(api_client: TestClient) -> None:
    camera_path = create_camera_path(api_client)
    zone = api_client.post(f"{camera_path}/zones", json={"name": "Door", "polygon": SQUARE}).json()

    response = api_client.post(
        f"{camera_path}/rules",
        json={
            "kind": "zone_occupancy",
            "zone_id": zone["id"],
            "object_classes": ["person"],
            "minimum_dwell_seconds": 5,
        },
    )

    assert response.status_code == 201
    assert response.json()["zone_id"] == zone["id"]
    assert [rule["id"] for rule in api_client.get(f"{camera_path}/rules").json()] == [
        response.json()["id"]
    ]


def test_zone_rule_is_rejected_when_zone_belongs_to_another_camera(api_client: TestClient) -> None:
    first_camera_path = create_camera_path(api_client)
    second_camera_path = create_camera_path(api_client)
    zone = api_client.post(
        f"{first_camera_path}/zones", json={"name": "Door", "polygon": SQUARE}
    ).json()

    response = api_client.post(
        f"{second_camera_path}/rules", json={"kind": "zone_occupancy", "zone_id": zone["id"]}
    )

    assert response.status_code == 404


def test_line_rule_update_is_rejected_when_it_sets_a_dwell_time(api_client: TestClient) -> None:
    camera_path = create_camera_path(api_client)
    rule = api_client.post(
        f"{camera_path}/rules",
        json={
            "kind": "line_crossing",
            "line_start": {"x": 0.5, "y": 0.0},
            "line_end": {"x": 0.5, "y": 1.0},
        },
    ).json()

    rejected = api_client.patch(
        f"{camera_path}/rules/{rule['id']}", json={"minimum_dwell_seconds": 3}
    )
    accepted = api_client.patch(
        f"{camera_path}/rules/{rule['id']}", json={"direction": "left_to_right"}
    )

    assert rejected.status_code == 422
    assert accepted.json()["direction"] == "left_to_right"


def test_zone_is_rejected_when_its_polygon_has_too_few_points(api_client: TestClient) -> None:
    camera_path = create_camera_path(api_client)

    response = api_client.post(f"{camera_path}/zones", json={"name": "Door", "polygon": SQUARE[:2]})

    assert response.status_code == 422


def test_deleting_a_zone_deletes_its_rules(api_client: TestClient) -> None:
    camera_path = create_camera_path(api_client)
    zone = api_client.post(f"{camera_path}/zones", json={"name": "Door", "polygon": SQUARE}).json()
    api_client.post(f"{camera_path}/rules", json={"kind": "zone_occupancy", "zone_id": zone["id"]})

    api_client.delete(f"{camera_path}/zones/{zone['id']}")

    assert api_client.get(f"{camera_path}/rules").json() == []
