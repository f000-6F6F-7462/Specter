"""PyAV/ffmpeg capture — the fallback ``FrameSource`` for environments without the full
GStreamer plugin set (``av`` vendors its own ffmpeg build, so no system packages beyond
the wheel itself), and the engine ``specter replay`` points at a local file to run the
real pipeline against recorded footage instead of a live camera.

Mirrors :class:`~specter.infrastructure.media.gstreamer.GStreamerFrameSource`'s shape:
a private thread runs the (blocking) decode loop and publishes onto a depth-2
``asyncio.Queue``. Unlike the live GStreamer path, ``drop_when_full`` can be turned off
so a fixed recording is replayed frame-for-frame rather than shedding frames whenever a
slow consumer falls behind — the two use cases (live capture vs. deterministic replay)
want opposite backpressure behaviour from the same adapter.
"""

import asyncio
import contextlib
import threading
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from urllib.parse import quote

from specter.core.errors import ConfigurationError, DependencyFailure
from specter.domain.streams import StreamProtocol, StreamSource
from specter.domain.vision import Frame


def _with_credentials(source: StreamSource) -> str:
    if source.credentials is None:
        return source.url
    scheme, sep, rest = source.url.partition("://")
    if not sep:
        raise ConfigurationError(f"cannot attach credentials to malformed url {source.url!r}")
    user = quote(source.credentials.username, safe="")
    secret = quote(source.credentials.password, safe="")
    return f"{scheme}://{user}:{secret}@{rest}"


def _open_options(source: StreamSource) -> dict[str, str]:
    if source.protocol == StreamProtocol.RTSP:
        return {"rtsp_transport": source.transport.value}
    return {}


class PyAvFrameSource:
    """A live (or file) PyAV connection. Iterating yields decoded BGR frames."""

    def __init__(
        self, stream_id: str, source: StreamSource, *, drop_when_full: bool = True
    ) -> None:
        self._stream_id = stream_id
        self._source = source
        self._drop_when_full = drop_when_full
        self._queue: asyncio.Queue[Frame | None] = asyncio.Queue(maxsize=2)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
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
        self._loop = asyncio.get_running_loop()
        self._thread = threading.Thread(
            target=self._run, name=f"pyav:{self._stream_id}", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        import av  # pylint: disable=import-outside-toplevel

        try:
            container = av.open(
                _with_credentials(self._source), options=_open_options(self._source), timeout=10.0
            )
        except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            self._finish(DependencyFailure(f"pyav stream {self._stream_id}: {exc}"))
            return
        try:
            for av_frame in container.decode(video=0):
                if self._closed:
                    break
                image = av_frame.to_ndarray(format="bgr24")
                self._seq += 1
                ts = float(av_frame.time) if av_frame.time is not None else float(self._seq)
                self._publish(
                    Frame(
                        stream_id=self._stream_id,
                        seq=self._seq,
                        ts=ts,
                        image=image,
                        captured_at=datetime.now(UTC),
                    )
                )
        except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            self._finish(DependencyFailure(f"pyav stream {self._stream_id}: {exc}"))
            return
        finally:
            container.close()
        self._finish(None)

    def _publish(self, frame: Frame) -> None:
        self._push(frame)

    def _finish(self, error: Exception | None) -> None:
        if error is not None:
            self._error = error
        self._push(None)

    def _push(self, item: Frame | None) -> None:
        """Runs on the decode thread. Live mode (``drop_when_full``) mirrors the
        GStreamer source — never blocks the decoder, newest frame wins. Replay mode
        blocks the decode thread until the consumer has room, so nothing is lost."""
        if self._loop is None:
            return
        if self._drop_when_full:
            self._loop.call_soon_threadsafe(self._offer_drop, item)
        else:
            # Bounded, not infinite: a consumer that stopped reading (early `break`)
            # must not leak this thread blocked on `put()` forever.
            asyncio.run_coroutine_threadsafe(self._queue.put(item), self._loop).result(timeout=30.0)

    def _offer_drop(self, item: Frame | None) -> None:
        if self._queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
        self._queue.put_nowait(item)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Only the decode thread itself ever touches `self._container` (it closes it
        # in `_run`'s own `finally`) — PyAV's container isn't safe to close from a
        # different thread while that thread's decode generator is still iterating it.
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join, 5.0)
