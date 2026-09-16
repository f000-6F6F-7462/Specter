import asyncio
import os
import shutil
import socket
import subprocess
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

from specter.camera_manager.go2rtc import Go2rtcClient
from specter.config.settings import MatchingSettings
from specter.messaging.client import MessageBus

TEST_NATS_URL_ENVIRONMENT_VARIABLE = "SPECTER_TEST_NATS_URL"
NATS_SERVER_START_TIMEOUT_SECONDS = 5.0
NATS_CONNECT_TIMEOUT_SECONDS = 10.0
TEST_GO2RTC_URL_ENVIRONMENT_VARIABLE = "SPECTER_TEST_GO2RTC_URL"
DEFAULT_TEST_GO2RTC_URL = "http://127.0.0.1:1984"
TEST_GO2RTC_RTSP_URL_ENVIRONMENT_VARIABLE = "SPECTER_TEST_GO2RTC_RTSP_URL"
DEFAULT_TEST_GO2RTC_RTSP_URL = "rtsp://127.0.0.1:8554"
# go2rtc generates this H.264 test pattern itself, so video tests need no camera.
VIRTUAL_VIDEO_SOURCE_URL = "ffmpeg:virtual?video&size=720#video=h264"
NATS_SERVER_INSTALLATION_URL = (
    "https://docs.nats.io/running-a-nats-service/introduction/installation"
)


def find_free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def wait_until_port_accepts_connections(port: int, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)
    pytest.fail(f"nats-server did not accept connections on port {port} in time")


@pytest.fixture
def nats_server_url(tmp_path: Path) -> Iterator[str]:
    # A server that is already running (for example from `make services-up`) is shared
    # between runs, so tests must not assume they start from an empty server.
    external_nats_url = os.environ.get(TEST_NATS_URL_ENVIRONMENT_VARIABLE)
    if external_nats_url:
        yield external_nats_url
        return

    executable = shutil.which("nats-server")
    if executable is None:
        pytest.fail(
            f"nats-server is not installed ({NATS_SERVER_INSTALLATION_URL}); "
            f"or set {TEST_NATS_URL_ENVIRONMENT_VARIABLE} to a running server"
        )
    port = find_free_port()
    server_process = subprocess.Popen(
        [
            executable,
            "--addr",
            "127.0.0.1",
            "--port",
            str(port),
            "--jetstream",
            "--store_dir",
            str(tmp_path / "jetstream"),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_until_port_accepts_connections(port, NATS_SERVER_START_TIMEOUT_SECONDS)
        yield f"nats://127.0.0.1:{port}"
    finally:
        server_process.terminate()
        server_process.wait(timeout=NATS_SERVER_START_TIMEOUT_SECONDS)


@pytest.fixture
async def message_bus(nats_server_url: str) -> AsyncIterator[MessageBus]:
    connected_message_bus = MessageBus(client_name="specter-tests")
    # Connecting retries forever, so an unreachable server must fail the test instead of hanging it.
    try:
        await asyncio.wait_for(
            connected_message_bus.connect(nats_server_url), NATS_CONNECT_TIMEOUT_SECONDS
        )
    except TimeoutError:
        await connected_message_bus.close()
        pytest.fail(f"NATS at {nats_server_url} is not reachable; start it with `make services-up`")
    await connected_message_bus.declare_streams(MatchingSettings().cooldown_seconds)
    yield connected_message_bus
    await connected_message_bus.close()


@pytest.fixture
def unique_camera_id() -> str:
    return f"test_camera_{time.time_ns()}"


@pytest.fixture
def unique_owner_id() -> str:
    return f"test_owner_{time.time_ns()}"


@pytest.fixture
def go2rtc_api_url() -> str:
    return os.environ.get(TEST_GO2RTC_URL_ENVIRONMENT_VARIABLE, DEFAULT_TEST_GO2RTC_URL)


@pytest.fixture
def go2rtc_rtsp_url() -> str:
    return os.environ.get(TEST_GO2RTC_RTSP_URL_ENVIRONMENT_VARIABLE, DEFAULT_TEST_GO2RTC_RTSP_URL)


@pytest.fixture
def virtual_video_source_url() -> str:
    return VIRTUAL_VIDEO_SOURCE_URL


@pytest.fixture
async def go2rtc_client(go2rtc_api_url: str) -> AsyncIterator[Go2rtcClient]:
    client = Go2rtcClient(go2rtc_api_url)
    yield client
    await client.close()
