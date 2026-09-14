"""The GStreamer pipeline-string builder is pure — no ``gi`` needed to exercise it."""

import pytest

from specter.core.errors import ConfigurationError
from specter.domain.streams import (
    StreamCredentials,
    StreamProtocol,
    StreamSource,
    TransportProtocol,
)
from specter.infrastructure.media.gstreamer import (
    build_pipeline_description,
    redacted_description,
)


def _source(**over: object) -> StreamSource:
    kwargs: dict[str, object] = {
        "protocol": StreamProtocol.RTSP,
        "url": "rtsp://cam/1",
    }
    kwargs.update(over)
    return StreamSource(**kwargs)  # type: ignore[arg-type]


def test_rtsp_pipeline_ends_at_a_bgr_appsink() -> None:
    desc = build_pipeline_description(_source(transport=TransportProtocol.TCP))
    assert desc.startswith("rtspsrc location=rtsp://cam/1")
    assert "protocols=tcp" in desc
    assert "video/x-raw,format=BGR" in desc
    assert desc.rstrip().endswith("appsink name=sink max-buffers=2 drop=true sync=false")


def test_transport_is_carried_through() -> None:
    assert "protocols=udp" in build_pipeline_description(_source(transport=TransportProtocol.UDP))


def test_credentials_are_url_encoded_into_the_location() -> None:
    desc = build_pipeline_description(
        _source(credentials=StreamCredentials(username="a b", password="p@ss/w"))
    )
    assert "location=rtsp://a%20b:p%40ss%2Fw@cam/1" in desc


@pytest.mark.parametrize(
    ("protocol", "url", "needle"),
    [
        (StreamProtocol.RTMP, "rtmp://cam/live", "rtmpsrc location=rtmp://cam/live ! flvdemux"),
        (
            StreamProtocol.HLS,
            "http://cam/i.m3u8",
            "souphttpsrc location=http://cam/i.m3u8 ! hlsdemux",
        ),
        (
            StreamProtocol.HTTP_FLV,
            "http://cam/s.flv",
            "souphttpsrc location=http://cam/s.flv ! flvdemux",
        ),
    ],
)
def test_source_element_per_protocol(protocol: StreamProtocol, url: str, needle: str) -> None:
    assert needle in build_pipeline_description(_source(protocol=protocol, url=url))


def test_webrtc_is_rejected() -> None:
    with pytest.raises(ConfigurationError):
        build_pipeline_description(_source(protocol=StreamProtocol.WEBRTC, url="https://cam/rtc"))


def test_sink_name_is_configurable() -> None:
    assert "appsink name=out " in build_pipeline_description(_source(), sink_name="out")


def test_redaction_masks_the_password_for_logging() -> None:
    desc = build_pipeline_description(
        _source(credentials=StreamCredentials(username="admin", password="hunter2"))
    )
    safe = redacted_description(desc)
    assert "hunter2" not in safe
    assert "admin:***@cam" in safe
