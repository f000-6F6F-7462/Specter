"""WebRTC capture via ``aiortc``, speaking WHEP (WebRTC-HTTP Egress Protocol) — the
IETF-drafted, now-widely-supported (MediaMTX, LiveKit, and most other WebRTC media
servers) convention for *pulling* a stream from a WebRTC publisher over plain HTTP
signaling. ``source.url`` is the server's WHEP endpoint; no bespoke signaling scheme is
invented here.

Handshake: POST an SDP offer (``recvonly`` video) as ``application/sdp``; the server's
SDP answer comes back as the response body (per the WHEP spec). ``source.credentials``,
if set, maps to WHEP's Bearer-token auth (``password`` is the token; ``username`` is
unused — WHEP auth is a single opaque token, not a username/password pair).

Unlike :class:`~specter.infrastructure.media.gstreamer.GStreamerFrameSource` / PyAV,
no background thread is needed — ``aiortc`` is asyncio-native throughout.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from specter.core.errors import ConfigurationError, DependencyFailure
from specter.domain.streams import StreamSource
from specter.domain.vision import Frame


class WebRtcFrameSource:
    def __init__(self, stream_id: str, source: StreamSource) -> None:
        _require_webrtc_url(source)
        self._stream_id = stream_id
        self._source = source
        self._pc: Any = None
        self._queue: asyncio.Queue[Frame | None] = asyncio.Queue(maxsize=2)
        self._seq = 0
        self._closed = False
        self._error: Exception | None = None
        self._pump_task: asyncio.Task[None] | None = None

    def __aiter__(self) -> AsyncIterator[Frame]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[Frame]:
        await self._connect()
        try:
            while True:
                item = await self._queue.get()
                if item is None:
                    break
                yield item
        finally:
            await self.aclose()
        if self._error is not None:
            raise self._error

    async def _connect(self) -> None:
        from aiortc import RTCPeerConnection  # pylint: disable=import-outside-toplevel

        pc = RTCPeerConnection()
        self._pc = pc

        @pc.on("track")
        def _on_track(track: Any) -> None:
            if track.kind == "video" and self._pump_task is None:
                self._pump_task = asyncio.ensure_future(self._pump(track))

        pc.addTransceiver("video", direction="recvonly")
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        assert pc.localDescription is not None  # noqa: S101 — set by setLocalDescription
        answer_sdp = await self._negotiate(pc.localDescription.sdp)

        from aiortc import RTCSessionDescription  # pylint: disable=import-outside-toplevel

        await pc.setRemoteDescription(RTCSessionDescription(sdp=answer_sdp, type="answer"))

    async def _negotiate(self, offer_sdp: str) -> str:
        import httpx  # pylint: disable=import-outside-toplevel

        headers = {"Content-Type": "application/sdp"}
        if self._source.credentials is not None:
            headers["Authorization"] = f"Bearer {self._source.credentials.password}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(self._source.url, content=offer_sdp, headers=headers)
                response.raise_for_status()
                return response.text
        except httpx.HTTPError as exc:
            raise DependencyFailure(
                f"webrtc stream {self._stream_id}: WHEP negotiation with "
                f"{self._source.url!r} failed: {exc}"
            ) from exc

    async def _pump(self, track: Any) -> None:
        try:
            while not self._closed:
                av_frame = await track.recv()
                image = av_frame.to_ndarray(format="bgr24")
                self._seq += 1
                ts = float(av_frame.time) if av_frame.time is not None else float(self._seq)
                self._offer(
                    Frame(
                        stream_id=self._stream_id,
                        seq=self._seq,
                        ts=ts,
                        image=image,
                        captured_at=datetime.now(UTC),
                    )
                )
        except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            if not self._closed:
                self._error = DependencyFailure(f"webrtc stream {self._stream_id}: {exc}")
        finally:
            self._offer(None)

    def _offer(self, item: Frame | None) -> None:
        if self._queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
        self._queue.put_nowait(item)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._pump_task is not None:
            self._pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pump_task
        if self._pc is not None:
            await self._pc.close()


def _require_webrtc_url(source: StreamSource) -> None:
    if not source.url.startswith(("http://", "https://")):
        raise ConfigurationError(f"WebRTC (WHEP) source url must be http(s), got {source.url!r}")
