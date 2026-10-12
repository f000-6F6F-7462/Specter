import httpx
import pytest

from specter.agent.camera_sync import CameraSyncReconciler


@pytest.mark.asyncio
async def test_camera_sync_creates_missing_cameras() -> None:
    recorded_requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        if request.method == "GET" and "/cameras" in str(request.url):
            # Local engine starts with 0 cameras
            return httpx.Response(200, json=[])
        if request.method == "POST" and "/start" in str(request.url):
            return httpx.Response(200, json={"id": "cam_1", "status": "running"})
        if request.method == "POST" and "/cameras" in str(request.url):
            return httpx.Response(
                201, json={"id": "cam_1", "name": "Gate", "source_url": "rtsp://cam1"}
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    client = httpx.AsyncClient(transport=transport)

    reconciler = CameraSyncReconciler(
        specter_api_url="http://127.0.0.1:8000",
        owner_id="specter",
        api_token="test-token",
        http_client=client,
    )

    cloud_cameras = [
        {
            "id": "cam_1",
            "name": "Gate",
            "source_url": "rtsp://cam1",
            "desired_state": "running",
            "watchlist_ids": ["wl_1"],
            "detection_classes": ["person"],
        }
    ]

    stats = await reconciler.reconcile(cloud_cameras)

    assert stats["created"] == 1
    assert stats["deleted"] == 0
    assert stats["updated"] == 0
    # Should have performed GET /cameras, POST /cameras, and POST /start
    assert len(recorded_requests) == 3


@pytest.mark.asyncio
async def test_camera_sync_updates_changed_cameras() -> None:
    recorded_requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "cam_1",
                        "name": "Old Gate",
                        "source_url": "rtsp://cam1",
                        "desired_state": "stopped",
                        "watchlist_ids": [],
                        "detection_classes": [],
                        "is_enabled": True,
                    }
                ],
            )
        if request.method == "PATCH":
            return httpx.Response(200, json={"id": "cam_1", "name": "New Gate"})
        if request.method == "POST" and "/start" in str(request.url):
            return httpx.Response(200, json={"id": "cam_1", "status": "running"})
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    client = httpx.AsyncClient(transport=transport)

    reconciler = CameraSyncReconciler(
        specter_api_url="http://127.0.0.1:8000",
        owner_id="specter",
        api_token="test-token",
        http_client=client,
    )

    cloud_cameras = [
        {
            "id": "cam_1",
            "name": "New Gate",
            "source_url": "rtsp://cam1",
            "desired_state": "running",
            "watchlist_ids": [],
            "detection_classes": [],
            "is_enabled": True,
        }
    ]

    stats = await reconciler.reconcile(cloud_cameras)

    assert stats["updated"] == 1
    assert stats["created"] == 0
    assert stats["deleted"] == 0


@pytest.mark.asyncio
async def test_camera_sync_deletes_removed_cameras() -> None:
    recorded_requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "cam_removed",
                        "name": "Old Cam",
                        "source_url": "rtsp://old",
                        "desired_state": "stopped",
                    }
                ],
            )
        if request.method in ("POST", "DELETE"):
            return httpx.Response(200, json={})
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    client = httpx.AsyncClient(transport=transport)

    reconciler = CameraSyncReconciler(
        specter_api_url="http://127.0.0.1:8000",
        owner_id="specter",
        api_token="test-token",
        http_client=client,
    )

    # Empty cloud cameras means cam_removed should be deleted
    stats = await reconciler.reconcile([])

    assert stats["deleted"] == 1
    assert stats["created"] == 0
    assert stats["updated"] == 0
