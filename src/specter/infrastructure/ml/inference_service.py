"""The shared inference layer: ``BatchedDetector`` / ``BatchedEmbedder`` coalesce the
``detect()``/``embed()`` calls from every concurrently-running stream in one
``specter-ingest`` process into shared forward passes, via one warmed model + one
``MicroBatcher`` each — so ten cameras calling a detector at once cost one batched
inference instead of ten.

Modeled as two thin decorators over the real adapters rather than a single merged
service class, so ``Detector`` and ``Embedder`` stay separate ports; DI wires them in
only around the real (non-fake) adapters — fakes stay direct-call for deterministic
tests. Each needs ``__aenter__``/``__aexit__`` to start/stop its ``MicroBatcher``'s
background task; ``Container.lifecycle`` is what enters and exits them.
"""

from collections.abc import Sequence
from typing import Protocol

import numpy as np

from specter.domain.vision import Crop, Detection, Embedding, Frame
from specter.infrastructure.ml.micro_batcher import MicroBatcher


class SyncFrameDetector(Protocol):
    def predict_sync(self, frames: list[Frame]) -> list[list[Detection]]: ...


class SyncImageEmbedder(Protocol):
    modality: str

    def embed_sync(self, images: list[np.ndarray]) -> list[np.ndarray]: ...


class BatchedDetector:
    def __init__(self, inner: SyncFrameDetector, *, max_batch: int, max_delay_ms: float) -> None:
        self._batcher = MicroBatcher(
            inner.predict_sync, max_batch=max_batch, max_delay_ms=max_delay_ms
        )

    async def __aenter__(self) -> "BatchedDetector":
        await self._batcher.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._batcher.__aexit__(*exc)

    async def detect(self, frames: Sequence[Frame]) -> list[list[Detection]]:
        if not frames:
            return []
        return await self._batcher.submit_many(list(frames))


class BatchedEmbedder:
    def __init__(self, inner: SyncImageEmbedder, *, max_batch: int, max_delay_ms: float) -> None:
        self.modality = inner.modality
        self._batcher = MicroBatcher(
            inner.embed_sync, max_batch=max_batch, max_delay_ms=max_delay_ms
        )

    async def __aenter__(self) -> "BatchedEmbedder":
        await self._batcher.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._batcher.__aexit__(*exc)

    async def embed(self, crops: Sequence[Crop]) -> list[Embedding]:
        if not crops:
            return []
        vectors = await self._batcher.submit_many([crop.image for crop in crops])
        return [Embedding(modality=self.modality, vector=vector) for vector in vectors]
