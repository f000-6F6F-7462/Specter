"""PyAvFrameSource: pure URL-building helpers directly, real decode via a tiny
self-generated local video (no live camera, no mocking av)."""

import asyncio

import numpy as np
import pytest

from specter.core.errors import ConfigurationError, DependencyFailure
from specter.domain.streams import (
    StreamCredentials,
    StreamProtocol,
    StreamSource,
    TransportProtocol,
)
from specter.infrastructure.media.pyav import PyAvFrameSource, _open_options, _with_credentials


def _source(**over: object) -> StreamSource:
    kwargs: dict[str, object] = {"protocol": StreamProtocol.RTSP, "url": "rtsp://cam/1"}
    kwargs.update(over)
    return StreamSource(**kwargs)  # type: ignore[arg-type]


class TestPureHelpers:
    def test_no_credentials_leaves_url_untouched(self) -> None:
        assert _with_credentials(_source()) == "rtsp://cam/1"

    def test_credentials_are_url_encoded(self) -> None:
        url = _with_credentials(
            _source(credentials=StreamCredentials(username="a b", password="p@ss/w"))
        )
        assert url == "rtsp://a%20b:p%40ss%2Fw@cam/1"

    def test_malformed_url_with_credentials_raises(self) -> None:
        with pytest.raises(ConfigurationError):
            _with_credentials(
                _source(url="not-a-url", credentials=StreamCredentials(username="a", password="b"))
            )

    def test_rtsp_carries_transport_option(self) -> None:
        assert _open_options(_source(transport=TransportProtocol.UDP)) == {"rtsp_transport": "udp"}

    def test_non_rtsp_has_no_options(self) -> None:
        assert _open_options(_source(protocol=StreamProtocol.HLS, url="http://cam/i.m3u8")) == {}


def _write_test_video(path: object, *, frames: int, size: tuple[int, int] = (32, 32)) -> None:
    import av

    container = av.open(str(path), mode="w")
    stream = container.add_stream("mpeg4", rate=10)
    stream.width, stream.height = size
    stream.pix_fmt = "yuv420p"
    for i in range(frames):
        arr = np.full((size[1], size[0], 3), (i * 40) % 256, dtype=np.uint8)
        vframe = av.VideoFrame.from_ndarray(arr, format="rgb24")
        for packet in stream.encode(vframe):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


class TestDecoding:
    async def test_yields_every_frame_in_order(self, tmp_path) -> None:
        video = tmp_path / "clip.mp4"
        _write_test_video(video, frames=8)
        source = PyAvFrameSource("st", _source(protocol=StreamProtocol.HLS, url=str(video)))

        seqs = []
        async with asyncio.timeout(10):
            async for frame in source:
                assert frame.image.shape == (32, 32, 3)
                seqs.append(frame.seq)
        assert seqs == list(range(1, 9))

    async def test_lossless_mode_never_drops_under_a_slow_consumer(self, tmp_path) -> None:
        video = tmp_path / "clip.mp4"
        _write_test_video(video, frames=12)
        source = PyAvFrameSource(
            "st",
            _source(protocol=StreamProtocol.HLS, url=str(video)),
            drop_when_full=False,
        )

        seqs = []
        async with asyncio.timeout(15):
            async for frame in source:
                await asyncio.sleep(0.01)  # slower than decode -> would drop in live mode
                seqs.append(frame.seq)
        assert seqs == list(range(1, 13))

    async def test_missing_file_raises_dependency_failure(self, tmp_path) -> None:
        source = PyAvFrameSource(
            "st", _source(protocol=StreamProtocol.HLS, url=str(tmp_path / "nope.mp4"))
        )
        with pytest.raises(DependencyFailure):
            async with asyncio.timeout(10):
                async for _ in source:
                    pass

    async def test_early_break_then_aclose_does_not_hang_or_crash(self, tmp_path) -> None:
        video = tmp_path / "clip.mp4"
        _write_test_video(video, frames=20)
        source = PyAvFrameSource("st", _source(protocol=StreamProtocol.HLS, url=str(video)))

        async with asyncio.timeout(10):
            async for frame in source:
                if frame.seq >= 2:
                    break
        await source.aclose()
