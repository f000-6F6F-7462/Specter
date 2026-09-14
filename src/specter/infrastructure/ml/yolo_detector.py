"""Real object detector: Ultralytics YOLO11.

``ultralytics`` is imported lazily (``[ml]`` extra) and the weights load on first use.
The forward pass runs on a worker thread — ``torch`` releases the GIL — so one shared
detector serves every stream in the ``specter-ingest`` process.
"""

import asyncio
from collections.abc import Sequence
from typing import Any

from specter.core.settings import DetectorSettings
from specter.domain.vision import Detection, Frame
from specter.infrastructure.ml._ultralytics_common import detections_from_result, overrides


class YoloDetector:
    model_version = "yolo11"

    def __init__(self, cfg: DetectorSettings) -> None:
        self._weights = cfg.weights
        self._device = cfg.device
        self._conf: float | None = cfg.conf
        self._iou: float | None = cfg.iou
        self._model: Any | None = None

    async def detect(self, frames: Sequence[Frame]) -> list[list[Detection]]:
        if not frames:
            return []
        return await asyncio.to_thread(self.predict_sync, list(frames))

    def _load(self) -> Any:
        if self._model is None:
            from ultralytics import YOLO  # pylint: disable=import-outside-toplevel

            model = YOLO(self._weights)
            model.to(self._device)
            self._model = model
        return self._model

    def predict_sync(self, frames: list[Frame]) -> list[list[Detection]]:
        """Synchronous batched forward pass — the seam ``BatchedDetector``'s
        ``MicroBatcher`` calls directly (via ``asyncio.to_thread``) to coalesce frames
        from every concurrently-running stream into one call."""
        model = self._load()
        results = model.predict(
            [frame.image for frame in frames],
            verbose=False,
            **overrides(self._conf, self._iou),
        )
        return [detections_from_result(result) for result in results]
