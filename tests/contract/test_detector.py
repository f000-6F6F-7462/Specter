"""Detector contract — holds for FakeDetector and YoloDetector alike."""

import numpy as np

from specter.application.ports import Detector
from specter.domain.vision import Frame


def _frame(seq: int) -> Frame:
    return Frame(
        stream_id="stream_ct",
        seq=seq,
        ts=float(seq),
        image=np.full((64, 64, 3), 127, dtype=np.uint8),
    )


async def test_detect_returns_one_list_per_frame(detector: Detector) -> None:
    out = await detector.detect([_frame(0), _frame(1)])

    assert len(out) == 2
    for per_frame in out:
        assert isinstance(per_frame, list)
        for detection in per_frame:
            assert detection.cls
            assert 0.0 <= detection.confidence <= 1.0
            assert detection.bbox.w > 0 and detection.bbox.h > 0


async def test_detect_of_nothing_is_empty(detector: Detector) -> None:
    assert await detector.detect([]) == []
