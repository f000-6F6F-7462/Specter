"""BatchedDetector / BatchedEmbedder — coalesce concurrent calls into one forward pass
via MicroBatcher, without changing the Detector/Embedder port shape.
"""

import asyncio

import numpy as np
import pytest

from specter.application.ports import Detector, Embedder
from specter.domain.vision import BBox, Crop, Detection, Frame
from specter.infrastructure.ml.inference_service import BatchedDetector, BatchedEmbedder

_BOX = BBox(x=0, y=0, w=4, h=4)


def _frame(seq: int) -> Frame:
    return Frame(stream_id="s", seq=seq, ts=float(seq), image=np.zeros((4, 4, 3), np.uint8))


def _crop(track_id: int) -> Crop:
    return Crop(
        stream_id="s",
        track_id=track_id,
        modality="face",
        image=np.zeros((4, 4, 3), np.uint8),
        quality=1.0,
    )


class _StubDetector:
    def __init__(self) -> None:
        self.calls: list[int] = []  # batch sizes seen per predict_sync call

    def predict_sync(self, frames: list[Frame]) -> list[list[Detection]]:
        self.calls.append(len(frames))
        return [[Detection(cls="face", confidence=0.9, bbox=_BOX)] for _ in frames]


class _StubEmbedder:
    modality = "face"

    def __init__(self) -> None:
        self.calls: list[int] = []

    def embed_sync(self, images: list[np.ndarray]) -> list[np.ndarray]:
        self.calls.append(len(images))
        return [np.ones(4, dtype=np.float32) for _ in images]


async def test_detect_of_nothing_never_touches_the_batcher() -> None:
    stub = _StubDetector()
    async with BatchedDetector(stub, max_batch=8, max_delay_ms=50) as detector:
        assert await detector.detect([]) == []
    assert stub.calls == []


async def test_concurrent_detect_calls_coalesce_into_one_forward_pass() -> None:
    stub = _StubDetector()
    async with BatchedDetector(stub, max_batch=8, max_delay_ms=200) as detector:
        assert isinstance(detector, Detector)
        results = await asyncio.gather(
            detector.detect([_frame(0)]),
            detector.detect([_frame(1)]),
            detector.detect([_frame(2)]),
        )
    assert all(r[0][0].cls == "face" for r in results)
    assert stub.calls == [3]  # three streams, one shared predict_sync call


async def test_embed_of_nothing_never_touches_the_batcher() -> None:
    stub = _StubEmbedder()
    async with BatchedEmbedder(stub, max_batch=8, max_delay_ms=50) as embedder:
        assert await embedder.embed([]) == []
    assert stub.calls == []


async def test_concurrent_embed_calls_coalesce_and_keep_the_modality() -> None:
    stub = _StubEmbedder()
    async with BatchedEmbedder(stub, max_batch=8, max_delay_ms=200) as embedder:
        assert isinstance(embedder, Embedder)
        assert embedder.modality == "face"
        results = await asyncio.gather(
            embedder.embed([_crop(1)]),
            embedder.embed([_crop(2)]),
        )
    assert all(r[0].modality == "face" for r in results)
    assert stub.calls == [2]  # two streams, one shared embed_sync call


async def test_a_model_failure_reaches_every_waiting_caller() -> None:
    class _Boom:
        def predict_sync(self, frames: list[Frame]) -> list[list[Detection]]:
            raise RuntimeError("model exploded")

    async with BatchedDetector(_Boom(), max_batch=8, max_delay_ms=200) as detector:
        with pytest.raises(RuntimeError, match="model exploded"):
            await asyncio.gather(detector.detect([_frame(0)]), detector.detect([_frame(1)]))
