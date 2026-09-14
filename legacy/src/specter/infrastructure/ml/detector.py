"""Deterministic object detector fakes for tests and for running without the ``[ml]``
extra.
"""

from collections.abc import Callable, Sequence

from specter.domain.vision import BBox, Detection, Frame

PerFrame = Callable[[Frame], Sequence[Detection]]


def center_box(cls: str = "face", *, confidence: float = 0.92, scale: float = 0.25) -> PerFrame:
    """One detection of ``cls`` covering the middle ``scale`` of every frame."""

    def _detect(frame: Frame) -> list[Detection]:
        height, width = frame.image.shape[0], frame.image.shape[1]
        box_w = max(int(width * scale), 1)
        box_h = max(int(height * scale), 1)
        box = BBox(x=(width - box_w) // 2, y=(height - box_h) // 2, w=box_w, h=box_h)
        return [Detection(cls=cls, confidence=confidence, bbox=box)]

    return _detect


def nothing() -> PerFrame:
    def _detect(_: Frame) -> list[Detection]:
        return []

    return _detect


class FakeDetector:
    def __init__(self, per_frame: PerFrame | None = None) -> None:
        self._per_frame = per_frame or center_box()

    async def detect(self, frames: Sequence[Frame]) -> list[list[Detection]]:
        return [list(self._per_frame(frame)) for frame in frames]
