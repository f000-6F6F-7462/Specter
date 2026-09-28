from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from specter.config.settings import Settings
from specter.entities.alerts import IdentityMatchAlert
from specter.entities.geometry import NormalizedBoundingBox
from specter.entities.targets import EmbeddingModality
from specter.storage.alerts import save_alert
from specter.storage.database import open_database
from specter.storage.evidence import EvidenceStore

OWNER_ID = "owner_alice"
ALERTS_PATH = f"/owners/{OWNER_ID}/alerts"
FIRST_ALERT_AT = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
ALERT_COUNT = 5
SNAPSHOT_BYTES = b"\xff\xd8\xff snapshot"


@pytest.fixture
def stored_alert_ids(api_settings: Settings) -> list[str]:
    evidence_store = EvidenceStore(api_settings.paths.data_directory)
    database = open_database(api_settings.paths.database_file)
    alert_ids: list[str] = []
    for index in range(ALERT_COUNT):
        created_at = FIRST_ALERT_AT + timedelta(minutes=index)
        alert_id = f"alert_{index}"
        snapshot_path = evidence_store.build_snapshot_path(OWNER_ID, alert_id, created_at)
        evidence_store.write_snapshot(snapshot_path, SNAPSHOT_BYTES)
        save_alert(
            IdentityMatchAlert(
                id=alert_id,
                owner_id=OWNER_ID,
                camera_id="camera_front_door",
                track_id=index,
                watchlist_id="watchlist_wanted",
                target_id="target_jane",
                modality=EmbeddingModality.FACE,
                similarity_ratio=0.8,
                margin_ratio=0.3,
                object_class="person",
                bounding_box=NormalizedBoundingBox(x=0.1, y=0.1, width=0.2, height=0.5),
                frame_captured_at=created_at,
                created_at=created_at,
                snapshot_path=snapshot_path,
            )
        )
        alert_ids.append(alert_id)
    database.close()
    return alert_ids


def test_alerts_are_listed_newest_first_across_pages(
    api_client: TestClient, stored_alert_ids: list[str]
) -> None:
    first_page = api_client.get(f"{ALERTS_PATH}/identity-matches", params={"limit": 3}).json()
    second_page = api_client.get(
        f"{ALERTS_PATH}/identity-matches", params={"limit": 3, "cursor": first_page["next_cursor"]}
    ).json()

    listed_ids = [alert["id"] for alert in first_page["alerts"] + second_page["alerts"]]
    assert listed_ids == list(reversed(stored_alert_ids))
    assert second_page["next_cursor"] is None


def test_resolving_an_alert_records_the_verdict(
    api_client: TestClient, stored_alert_ids: list[str]
) -> None:
    alert_path = f"{ALERTS_PATH}/{stored_alert_ids[0]}"

    api_client.post(f"{alert_path}/resolve", json={"disposition": "false_positive", "note": "twin"})
    unreviewed_page = api_client.get(
        f"{ALERTS_PATH}/identity-matches", params={"disposition": "unreviewed"}
    ).json()

    review = api_client.get(alert_path).json()["review"]
    assert review == {"disposition": "false_positive", "is_acknowledged": True, "note": "twin"}
    assert stored_alert_ids[0] not in [alert["id"] for alert in unreviewed_page["alerts"]]


def test_snapshot_is_served_only_to_the_alerts_owner(
    api_client: TestClient, stored_alert_ids: list[str]
) -> None:
    own_snapshot = api_client.get(f"{ALERTS_PATH}/{stored_alert_ids[0]}/snapshot")
    foreign_snapshot = api_client.get(f"/owners/owner_bob/alerts/{stored_alert_ids[0]}/snapshot")

    assert own_snapshot.status_code == 200
    assert own_snapshot.content == SNAPSHOT_BYTES
    assert own_snapshot.headers["content-type"] == "image/jpeg"
    assert foreign_snapshot.status_code == 404


def test_invalid_cursor_is_rejected(api_client: TestClient) -> None:
    response = api_client.get(f"{ALERTS_PATH}/identity-matches", params={"cursor": "not-a-cursor"})

    assert response.status_code == 422


def test_alerts_of_any_listed_camera_are_returned(
    api_client: TestClient, stored_alert_ids: list[str]
) -> None:
    listed = api_client.get(
        ALERTS_PATH, params=[("camera_id", "camera_front_door"), ("camera_id", "camera_garage")]
    ).json()
    other_camera = api_client.get(ALERTS_PATH, params={"camera_id": "camera_garage"}).json()

    assert [alert["id"] for alert in listed["alerts"]] == list(reversed(stored_alert_ids))
    assert other_camera["alerts"] == []


def test_summary_counts_unacknowledged_alerts(
    api_client: TestClient, stored_alert_ids: list[str]
) -> None:
    api_client.post(f"{ALERTS_PATH}/{stored_alert_ids[0]}/acknowledge")

    summary = api_client.get(f"{ALERTS_PATH}/summary").json()

    assert (summary["total_count"], summary["unacknowledged_count"]) == (
        ALERT_COUNT,
        ALERT_COUNT - 1,
    )
    assert summary["daily_counts"] == [
        {"day": "2026-09-16", "kind": "identity_match", "count": ALERT_COUNT}
    ]
