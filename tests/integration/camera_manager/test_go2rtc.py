import time

import httpx
import pytest

from specter.camera_manager.go2rtc import Go2rtcClient

pytestmark = pytest.mark.integration

REPLACEMENT_SOURCE_URL = "ffmpeg:virtual?video&size=1080#video=h264"


@pytest.fixture
def stream_name() -> str:
    # go2rtc is shared between test runs, so each test uses its own stream.
    return f"test_stream_{time.time_ns()}"


async def test_stream_is_listed_when_registered(
    go2rtc_client: Go2rtcClient, stream_name: str, virtual_video_source_url: str
) -> None:
    await go2rtc_client.register_stream(stream_name, virtual_video_source_url)
    try:
        assert stream_name in await go2rtc_client.list_stream_names()
    finally:
        await go2rtc_client.remove_stream(stream_name)


async def test_stream_source_is_replaced_when_registered_again(
    go2rtc_client: Go2rtcClient,
    go2rtc_api_url: str,
    stream_name: str,
    virtual_video_source_url: str,
) -> None:
    await go2rtc_client.register_stream(stream_name, virtual_video_source_url)
    try:
        await go2rtc_client.register_stream(stream_name, REPLACEMENT_SOURCE_URL)
        streams = httpx.get(f"{go2rtc_api_url}/api/streams").json()
    finally:
        await go2rtc_client.remove_stream(stream_name)

    source_urls = [producer["url"] for producer in streams[stream_name]["producers"]]
    assert source_urls == [REPLACEMENT_SOURCE_URL]


async def test_stream_is_gone_when_removed(
    go2rtc_client: Go2rtcClient, stream_name: str, virtual_video_source_url: str
) -> None:
    await go2rtc_client.register_stream(stream_name, virtual_video_source_url)

    await go2rtc_client.remove_stream(stream_name)

    assert stream_name not in await go2rtc_client.list_stream_names()


async def test_removing_succeeds_when_stream_is_not_registered(
    go2rtc_client: Go2rtcClient, stream_name: str
) -> None:
    await go2rtc_client.remove_stream(stream_name)
