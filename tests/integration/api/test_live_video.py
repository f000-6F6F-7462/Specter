import json
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from specter.api.routers.live_video import POLICY_VIOLATION_CLOSE_CODE

pytestmark = pytest.mark.integration


@pytest.fixture
def running_camera_path(
    connected_api_client: TestClient,
    go2rtc_api_url: str,
    virtual_video_source_url: str,
    unique_owner_id: str,
) -> Iterator[str]:
    camera = connected_api_client.post(
        f"/owners/{unique_owner_id}/cameras",
        json={"name": "Virtual", "source_url": virtual_video_source_url},
    ).json()
    camera_path = f"/owners/{unique_owner_id}/cameras/{camera['id']}"
    connected_api_client.post(f"{camera_path}/start")
    # The camera manager registers running cameras in go2rtc; this test does it in its place.
    httpx.patch(
        f"{go2rtc_api_url}/api/streams",
        params={"name": camera["id"], "src": virtual_video_source_url},
    ).raise_for_status()
    yield camera_path
    httpx.delete(f"{go2rtc_api_url}/api/streams", params={"src": camera["id"]})


def test_latest_frame_is_relayed_as_a_jpeg(
    connected_api_client: TestClient, running_camera_path: str
) -> None:
    response = connected_api_client.get(f"{running_camera_path}/live/frame.jpeg")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content.startswith(b"\xff\xd8")


def test_hls_playlist_links_are_served_through_the_api(
    connected_api_client: TestClient, running_camera_path: str
) -> None:
    master_playlist = connected_api_client.get(f"{running_camera_path}/live/stream.m3u8")
    media_playlist_link = next(
        line for line in master_playlist.text.splitlines() if line.startswith("hls/")
    )

    media_playlist = connected_api_client.get(f"{running_camera_path}/live/{media_playlist_link}")

    assert master_playlist.status_code == 200
    assert media_playlist.status_code == 200
    assert media_playlist.text.startswith("#EXTM3U")


def test_mse_websocket_relays_the_stream_description_and_video(
    connected_api_client: TestClient, running_camera_path: str
) -> None:
    with connected_api_client.websocket_connect(f"{running_camera_path}/live/mse") as websocket:
        websocket.send_text(json.dumps({"type": "mse", "value": "avc1.640029,mp4a.40.2"}))
        description = json.loads(websocket.receive_text())
        video_data = websocket.receive_bytes()

    assert description["type"] == "mse"
    assert len(video_data) > 0


def test_mse_websocket_is_closed_without_the_api_token(
    connected_api_client: TestClient, running_camera_path: str
) -> None:
    connected_api_client.headers.pop("Authorization")

    with (
        pytest.raises(WebSocketDisconnect) as disconnection,
        connected_api_client.websocket_connect(f"{running_camera_path}/live/mse") as websocket,
    ):
        websocket.receive_text()

    assert disconnection.value.code == POLICY_VIOLATION_CLOSE_CODE
