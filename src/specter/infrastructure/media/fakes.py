"""A frame source that synthesises frames instead of decoding a stream."""

import asyncio
from collections.abc import AsyncIterator

import numpy as np

from specter.domain.vision import Frame


class SyntheticFrameSource:
    """Yield ``count`` frames (or forever when ``count is None``).

    ``fps`` gates the cadence; leave it ``None`` for tests so frames come as fast as the
    consumer takes them. ``fail_after`` raises ``ConnectionError`` mid-stream to exercise
    supervisor restart. ``moving`` varies the pixels per frame so crops differ; the
    default fills every frame identically.
    """

    def __init__(
        self,
        stream_id: str,
        *,
        count: int | None = 30,
        size: tuple[int, int] = (64, 64),
        fps: float | None = None,
        fail_after: int | None = None,
        moving: bool = False,
        fill: int = 128,
    ) -> None:
        self.stream_id = stream_id
        self._count = count
        self._height, self._width = size
        self._interval = 1.0 / fps if fps else 0.0
        self._fail_after = fail_after
        self._moving = moving
        self._fill = fill
        self._closed = False

    def __aiter__(self) -> AsyncIterator[Frame]:
        return self._generate()

    async def aclose(self) -> None:
        self._closed = True

    async def _generate(self) -> AsyncIterator[Frame]:
        seq = 0
        while not self._closed and (self._count is None or seq < self._count):
            if self._fail_after is not None and seq >= self._fail_after:
                raise ConnectionError(f"synthetic stream {self.stream_id} dropped at frame {seq}")
            if self._interval:
                await asyncio.sleep(self._interval)
            yield Frame(
                stream_id=self.stream_id,
                seq=seq,
                ts=seq * (self._interval or 0.1),
                image=self._image(seq),
            )
            seq += 1

    def _image(self, seq: int) -> np.ndarray:
        value = (self._fill + seq) % 256 if self._moving else self._fill
        return np.full((self._height, self._width, 3), value, dtype=np.uint8)
