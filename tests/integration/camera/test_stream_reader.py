import asyncio
import time
from collections.abc import AsyncIterator

import pytest

from specter.camera.stream_reader import StreamReader
from specter.camera_manager.go2rtc import Go2rtcClient
from specter.entities.cameras import CameraStatus

pytestmark = pytest.mark.integration

FRAME_TIMEOUT_SECONDS = 30.0
STATUS_TIMEOUT_SECONDS = 15.0
STATUS_POLL_INTERVAL_SECONDS = 0.1
MAXIMUM_FRAME_SIZE_PIXELS = 640
# The virtual 1280x720 stream, scaled down to fit a 640-pixel square.
SCALED_VIDEO_SHAPE = (360, 640, 3)


@pytest.fixture
async def virtual_stream_name(
    go2rtc_client: Go2rtcClient, virtual_video_source_url: str
) -> AsyncIterator[str]:
    stream_name = f"test_stream_{time.time_ns()}"
    await go2rtc_client.register_stream(stream_name, virtual_video_source_url)
    yield stream_name
    await go2rtc_client.remove_stream(stream_name)


async def test_newer_frames_are_decoded_when_stream_is_available(
    go2rtc_rtsp_url: str, virtual_stream_name: str
) -> None:
    stream_reader = StreamReader(
        "camera_test",
        f"{go2rtc_rtsp_url}/{virtual_stream_name}",
        maximum_width_pixels=MAXIMUM_FRAME_SIZE_PIXELS,
        maximum_height_pixels=MAXIMUM_FRAME_SIZE_PIXELS,
    )
    stream_reader.start()
    try:
        first_frame = await asyncio.wait_for(stream_reader.next_frame(), FRAME_TIMEOUT_SECONDS)
        second_frame = await asyncio.wait_for(stream_reader.next_frame(), FRAME_TIMEOUT_SECONDS)
        status_while_reading = stream_reader.status
    finally:
        await stream_reader.stop()

    assert first_frame.image.shape == SCALED_VIDEO_SHAPE
    assert second_frame.sequence_number > first_frame.sequence_number
    assert status_while_reading is CameraStatus.RUNNING


async def test_status_is_reconnecting_when_stream_does_not_exist(go2rtc_rtsp_url: str) -> None:
    stream_reader = StreamReader(
        "camera_test",
        f"{go2rtc_rtsp_url}/missing_stream_{time.time_ns()}",
        maximum_width_pixels=MAXIMUM_FRAME_SIZE_PIXELS,
        maximum_height_pixels=MAXIMUM_FRAME_SIZE_PIXELS,
    )
    stream_reader.start()
    try:
        await wait_for_status(stream_reader, CameraStatus.RECONNECTING)
    finally:
        await stream_reader.stop()


async def wait_for_status(stream_reader: StreamReader, expected_status: CameraStatus) -> None:
    deadline = time.monotonic() + STATUS_TIMEOUT_SECONDS
    while stream_reader.status is not expected_status:
        if time.monotonic() > deadline:
            pytest.fail(f"stream reader stayed {stream_reader.status}, expected {expected_status}")
        await asyncio.sleep(STATUS_POLL_INTERVAL_SECONDS)
