"""WebRtcFrameSource: URL validation and the WHEP HTTP negotiation layer are tested
directly; the actual signaling + media path is proven with two *real* aiortc peer
connections talking to each other over loopback (the HTTP hop is the only thing
swapped out, for a direct in-process call instead of a real socket — aiortc/av
themselves are exercised for real, nothing about WebRTC itself is mocked)."""

import asyncio

import httpx
import pytest

from specter.core.errors import ConfigurationError, DependencyFailure
from specter.domain.streams import StreamCredentials, StreamProtocol, StreamSource
from specter.infrastructure.media.webrtc import WebRtcFrameSource


def _source(**over: object) -> StreamSource:
    kwargs: dict[str, object] = {"protocol": StreamProtocol.WEBRTC, "url": "https://cam/whep/1"}
    kwargs.update(over)
    return StreamSource(**kwargs)  # type: ignore[arg-type]


class TestUrlValidation:
    def test_https_url_is_accepted(self) -> None:
        WebRtcFrameSource("s1", _source(url="https://cam/whep/1"))

    def test_http_url_is_accepted(self) -> None:
        WebRtcFrameSource("s1", _source(url="http://cam/whep/1"))

    def test_non_http_url_is_rejected(self) -> None:
        with pytest.raises(ConfigurationError):
            WebRtcFrameSource("s1", _source(url="rtsp://cam/1"))


def _patch_transport(monkeypatch: pytest.MonkeyPatch, handler: object) -> None:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw))


class TestNegotiateHttpLayer:
    async def test_posts_the_offer_as_application_sdp(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            captured["content_type"] = request.headers.get("content-type")
            captured["body"] = request.content.decode()
            captured["auth"] = request.headers.get("authorization")
            return httpx.Response(201, text="v=0\r\no=- 0 0 IN IP4 0.0.0.0\r\n")

        _patch_transport(monkeypatch, handler)
        source = WebRtcFrameSource("s1", _source())

        answer = await source._negotiate("v=0\r\no=- 1 1 IN IP4 0.0.0.0\r\n")  # noqa: SLF001

        assert captured["method"] == "POST"
        assert captured["content_type"] == "application/sdp"
        assert str(captured["body"]).startswith("v=0")
        assert captured["auth"] is None
        assert answer.startswith("v=0")

    async def test_credentials_map_to_a_bearer_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["auth"] = request.headers.get("authorization")
            return httpx.Response(201, text="v=0\r\n")

        _patch_transport(monkeypatch, handler)
        source = WebRtcFrameSource(
            "s1", _source(credentials=StreamCredentials(username="ignored", password="tok123"))
        )

        await source._negotiate("v=0\r\n")  # noqa: SLF001

        assert captured["auth"] == "Bearer tok123"

    async def test_http_error_becomes_a_dependency_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="nope")

        _patch_transport(monkeypatch, handler)
        source = WebRtcFrameSource("s1", _source())

        with pytest.raises(DependencyFailure):
            await source._negotiate("v=0\r\n")  # noqa: SLF001


class _FakeWhepServer:
    """A real WHEP responder for the E2E test: a genuine aiortc RTCPeerConnection,
    publishing a real (synthetic, green) video track."""

    def __init__(self) -> None:
        self.pc: object | None = None

    async def handle_offer(self, offer_sdp: str) -> str:
        from aiortc import RTCPeerConnection, RTCSessionDescription
        from aiortc.mediastreams import VideoStreamTrack

        pc = RTCPeerConnection()
        self.pc = pc
        pc.addTrack(VideoStreamTrack())
        await pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type="offer"))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        assert pc.localDescription is not None  # noqa: S101
        return pc.localDescription.sdp

    async def close(self) -> None:
        if self.pc is not None:
            await self.pc.close()  # type: ignore[attr-defined]


async def test_real_two_peer_webrtc_session_yields_real_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two genuine aiortc peer connections negotiate over loopback and stream real
    video — only the HTTP transport of the WHEP handshake is swapped for a direct
    call; aiortc/av themselves are never mocked."""
    # A test-machine's multiple real network interfaces (IPv6, a NAT'd public IPv4,
    # ...) give ICE candidate pairs that can't actually route to each other on the
    # same host — restricting candidate gathering to loopback is what makes two local
    # peer connections reliably reach each other here; real deployments talk to a real
    # remote WHEP server and don't need this.
    import aioice.ice as ice_mod

    monkeypatch.setattr(
        ice_mod, "get_host_addresses", lambda use_ipv4, use_ipv6: ["127.0.0.1"] if use_ipv4 else []
    )

    server = _FakeWhepServer()
    source = WebRtcFrameSource("s1", _source())
    monkeypatch.setattr(source, "_negotiate", server.handle_offer)

    frames = []
    try:
        async with asyncio.timeout(20):
            async for frame in source:
                frames.append(frame)
                if len(frames) >= 3:
                    break
    finally:
        await source.aclose()
        await server.close()

    assert len(frames) == 3
    assert [f.seq for f in frames] == [1, 2, 3]
    for frame in frames:
        assert frame.stream_id == "s1"
        assert frame.image.shape == (480, 640, 3)
