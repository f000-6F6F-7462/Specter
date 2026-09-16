import asyncio
import time
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import nats
import pytest
from nats.aio.msg import Msg

from specter.camera_manager.go2rtc import Go2rtcClient
from specter.camera_manager.main import supervise_cameras
from specter.config.loading import CONFIG_FILE_ENVIRONMENT_VARIABLE, load_settings
from specter.config.settings import Settings
from specter.entities.cameras import Camera, CameraStatus, DesiredState
from specter.messaging.client import MessageBus
from specter.messaging.messages import (
    CameraStatusChangedMessage,
    ChangeKind,
    ConfigurationChangedMessage,
    EntityKind,
)
from specter.messaging.subjects import CameraEvent, build_camera_subject
from specter.storage.cameras import save_camera
from specter.storage.database import open_database
from specter.storage.migrate import apply_migrations

pytestmark = pytest.mark.integration

STATUS_TIMEOUT_SECONDS = 60.0
MANAGER_STOP_TIMEOUT_SECONDS = 30.0
STREAM_REMOVAL_TIMEOUT_SECONDS = 10.0
STREAM_POLL_INTERVAL_SECONDS = 0.2


@pytest.fixture
def device_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    nats_server_url: str,
    go2rtc_api_url: str,
    go2rtc_rtsp_url: str,
) -> Settings:
    # Camera processes are separate interpreters, so like on a device they read their settings
    # from the environment they inherit.
    monkeypatch.delenv(CONFIG_FILE_ENVIRONMENT_VARIABLE, raising=False)
    monkeypatch.setenv("SPECTER_PATHS__DATA_DIRECTORY", str(tmp_path / "data"))
    monkeypatch.setenv(
        "SPECTER_SECURITY__CREDENTIALS_KEY_FILE", str(tmp_path / "secrets" / "credentials.key")
    )
    monkeypatch.setenv("SPECTER_SERVICES__NATS_URL", nats_server_url)
    monkeypatch.setenv("SPECTER_SERVICES__GO2RTC_URL", go2rtc_api_url)
    monkeypatch.setenv("SPECTER_SERVICES__GO2RTC_RTSP_URL", go2rtc_rtsp_url)
    return load_settings()


async def test_camera_process_runs_and_stops_when_its_desired_state_changes(
    device_settings: Settings,
    message_bus: MessageBus,
    nats_server_url: str,
    go2rtc_client: Go2rtcClient,
    virtual_video_source_url: str,
) -> None:
    camera = Camera(
        id=f"camera_test_{time.time_ns()}",
        owner_id="owner_tests",
        name="Virtual camera",
        source_url=virtual_video_source_url,
        desired_state=DesiredState.RUNNING,
    )
    database = open_database(device_settings.paths.database_file)
    apply_migrations(database)
    save_camera(camera, None)
    statuses: asyncio.Queue[CameraStatus] = asyncio.Queue()
    # External clients read camera statuses straight from JetStream, and so does this test.
    status_connection = await nats.connect(nats_server_url)
    await status_connection.jetstream().subscribe(
        build_camera_subject(camera.owner_id, camera.id, CameraEvent.STATUS_CHANGED),
        cb=partial(record_status, statuses=statuses),
        ordered_consumer=True,
    )
    shutdown_requested = asyncio.Event()
    manager_task = asyncio.create_task(
        supervise_cameras(device_settings, message_bus, shutdown_requested)
    )
    try:
        await wait_for_status(statuses, CameraStatus.RUNNING)
        stream_names_while_running = await go2rtc_client.list_stream_names()

        save_camera(camera.stop(), None)
        await message_bus.publish(
            ConfigurationChangedMessage(
                occurred_at=datetime.now(UTC),
                owner_id=camera.owner_id,
                entity_kind=EntityKind.CAMERA,
                entity_id=camera.id,
                change_kind=ChangeKind.UPDATED,
            )
        )
        await wait_for_status(statuses, CameraStatus.STOPPED)
        # The manager removes the stream only after the camera's process has stopped.
        is_stream_removed = await wait_for_stream_removal(go2rtc_client, camera.id)
    finally:
        shutdown_requested.set()
        await asyncio.wait_for(manager_task, MANAGER_STOP_TIMEOUT_SECONDS)
        await status_connection.close()
        database.close()

    assert camera.id in stream_names_while_running
    assert is_stream_removed


async def record_status(raw_message: Msg, *, statuses: asyncio.Queue[CameraStatus]) -> None:
    await statuses.put(CameraStatusChangedMessage.model_validate_json(raw_message.data).status)


async def wait_for_status(
    statuses: asyncio.Queue[CameraStatus], expected_status: CameraStatus
) -> None:
    deadline = time.monotonic() + STATUS_TIMEOUT_SECONDS
    status = None
    while status is not expected_status:
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            pytest.fail(f"camera status stayed {status}, expected {expected_status}")
        try:
            status = await asyncio.wait_for(statuses.get(), remaining_seconds)
        except TimeoutError:
            pytest.fail(f"camera status stayed {status}, expected {expected_status}")


async def wait_for_stream_removal(go2rtc_client: Go2rtcClient, stream_name: str) -> bool:
    deadline = time.monotonic() + STREAM_REMOVAL_TIMEOUT_SECONDS
    while stream_name in await go2rtc_client.list_stream_names():
        if time.monotonic() > deadline:
            return False
        await asyncio.sleep(STREAM_POLL_INTERVAL_SECONDS)
    return True
