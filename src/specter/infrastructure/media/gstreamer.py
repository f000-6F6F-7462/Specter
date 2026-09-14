"""GStreamer capture — the primary ``FrameSource``.

``build_pipeline_description`` is a pure function: it turns a :class:`StreamSource` into
a ``gst-launch``-style pipeline string, one source element per protocol, decoded to
packed ``BGR`` and pulled from an ``appsink`` capped at two buffers with ``drop=true``
so the decoder never hands us stale video.

``GStreamerFrameSource`` runs that pipeline on a private ``GLib`` main loop thread and
publishes the freshest decoded frame onto a depth-2 :class:`asyncio.Queue` (newest
wins). ``gi`` is imported lazily so the rest of the engine — and the default test gate —
runs without the GStreamer stack. Requires the ``[media]`` extra plus the system
GStreamer plugins.
"""

import asyncio
import contextlib
import logging
import re
import threading
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import numpy as np

from specter.core.errors import ConfigurationError, DependencyFailure
from specter.domain.streams import StreamProtocol, StreamSource
from specter.domain.vision import Frame

# ``gi`` / GStreamer come from the [media] extra and the system plugin set. They are
# imported lazily (mirroring the [ml] adapters) so the default gate never needs them;
# the DI selector only reaches this module when SPECTER_MEDIA=gstreamer.

log = logging.getLogger(__name__)

_SINK_TAIL = (
    "videoconvert ! video/x-raw,format=BGR "
    "! appsink name={sink} max-buffers=2 drop=true sync=false"
)
_CREDENTIALS_RE = re.compile(r"://([^/:@]+):([^/@]+)@")


def build_pipeline_description(source: StreamSource, *, sink_name: str = "sink") -> str:
    """Render the capture pipeline for ``source``. Raises for protocols GStreamer does
    not own here (WebRTC stays on the aiortc source)."""
    location = _with_credentials(source)
    match source.protocol:
        case StreamProtocol.RTSP:
            head = (
                f"rtspsrc location={location} latency=100 "
                f"protocols={source.transport.value} ! rtph264depay ! h264parse ! decodebin"
            )
        case StreamProtocol.RTMP:
            head = f"rtmpsrc location={location} ! flvdemux ! h264parse ! decodebin"
        case StreamProtocol.HLS:
            head = f"souphttpsrc location={location} ! hlsdemux ! decodebin"
        case StreamProtocol.HTTP_FLV:
            head = f"souphttpsrc location={location} ! flvdemux ! decodebin"
        case _:
            raise ConfigurationError(
                f"{source.protocol.value!r} capture is not served by the GStreamer source"
            )
    return f"{head} ! {_SINK_TAIL.format(sink=sink_name)}"


def redacted_description(description: str) -> str:
    """The pipeline string with any ``user:password@`` masked — safe to log."""
    return _CREDENTIALS_RE.sub(lambda m: f"://{m.group(1)}:***@", description)


def _with_credentials(source: StreamSource) -> str:
    if source.credentials is None:
        return source.url
    scheme, sep, rest = source.url.partition("://")
    if not sep:
        raise ConfigurationError(f"cannot attach credentials to malformed url {source.url!r}")
    user = quote(source.credentials.username, safe="")
    secret = quote(source.credentials.password, safe="")
    return f"{scheme}://{user}:{secret}@{rest}"


class GStreamerFrameSource:
    """A live GStreamer connection. Iterating yields the freshest decoded frame."""

    def __init__(self, stream_id: str, source: StreamSource, *, sink_name: str = "sink") -> None:
        self._stream_id = stream_id
        self._description = build_pipeline_description(source, sink_name=sink_name)
        self._sink_name = sink_name
        self._queue: asyncio.Queue[Frame | None] = asyncio.Queue(maxsize=2)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._pipeline: Any = None
        self._glib_loop: Any = None
        self._seq = 0
        self._closed = False
        self._error: Exception | None = None

    def __aiter__(self) -> AsyncIterator[Frame]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[Frame]:
        self._start()
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

    def _start(self) -> None:
        if self._thread is not None:
            return
        gst = _load_gst()
        self._loop = asyncio.get_running_loop()
        self._pipeline = gst.parse_launch(self._description)
        sink = self._pipeline.get_by_name(self._sink_name)
        sink.set_property("emit-signals", True)
        sink.connect("new-sample", self._on_sample, gst)
        bus = self._pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus_message, gst)
        self._glib_loop = _glib().MainLoop()
        self._pipeline.set_state(gst.State.PLAYING)
        self._thread = threading.Thread(
            target=self._glib_loop.run, name=f"gst:{self._stream_id}", daemon=True
        )
        self._thread.start()
        log.info(
            "gstreamer stream %s started: %s",
            self._stream_id,
            redacted_description(self._description),
        )

    def _on_sample(self, sink: Any, gst: Any) -> Any:
        sample = sink.emit("pull-sample")
        if sample is None:
            return gst.FlowReturn.OK
        frame = self._to_frame(sample, gst)
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._offer, frame)
        return gst.FlowReturn.OK

    def _to_frame(self, sample: Any, gst: Any) -> Frame:
        buffer = sample.get_buffer()
        caps = sample.get_caps().get_structure(0)
        width, height = int(caps.get_value("width")), int(caps.get_value("height"))
        ok, mapinfo = buffer.map(gst.MapFlags.READ)
        if not ok:
            raise DependencyFailure("failed to map a GStreamer buffer")
        try:
            # Copy out of the mapped buffer; the zero-copy view would dangle after unmap.
            image = np.frombuffer(mapinfo.data, dtype=np.uint8).reshape(height, width, 3).copy()
        finally:
            buffer.unmap(mapinfo)
        self._seq += 1
        pts = buffer.pts
        ts = float(pts) / gst.SECOND if pts != gst.CLOCK_TIME_NONE else float(self._seq)
        return Frame(
            stream_id=self._stream_id,
            seq=self._seq,
            ts=ts,
            image=image,
            captured_at=datetime.now(UTC),
        )

    def _offer(self, frame: Frame) -> None:
        if self._queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
        self._queue.put_nowait(frame)

    def _on_bus_message(self, _bus: Any, message: Any, gst: Any) -> None:
        if message.type == gst.MessageType.EOS:
            self._finish(None)
        elif message.type == gst.MessageType.ERROR:
            err, _debug = message.parse_error()
            self._finish(DependencyFailure(f"gstreamer stream {self._stream_id}: {err.message}"))

    def _finish(self, error: Exception | None) -> None:
        if self._loop is None:
            return

        def _publish() -> None:
            if error is not None and self._error is None:
                self._error = error
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait(None)

        self._loop.call_soon_threadsafe(_publish)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._pipeline is not None:
            self._pipeline.set_state(_load_gst().State.NULL)
        if self._glib_loop is not None:
            self._glib_loop.quit()
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join, 5.0)


def _load_gst() -> Any:
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    if not Gst.is_initialized():
        Gst.init(None)
    return Gst


def _glib() -> Any:
    import gi

    gi.require_version("GLib", "2.0")
    from gi.repository import GLib

    return GLib
