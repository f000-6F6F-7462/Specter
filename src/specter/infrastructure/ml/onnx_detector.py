"""ONNX Runtime detector: ``ultralytics.YOLO`` dispatches to its own ``ONNXBackend``
when given ``.onnx`` weights (``AutoBackend`` sniffs the extension) — which, for
``device="mps"``, itself selects onnxruntime's ``CoreMLExecutionProvider`` (falling back
to ``CPUExecutionProvider`` if unavailable) rather than native PyTorch/MPS. Same pre/
post-processing as :class:`~specter.infrastructure.ml.yolo_detector.YoloDetector` (see
``_ultralytics_common``) — the two exist as separate adapters because they commit to
different runtimes for the same weights family, not because detection logic differs.

Export weights with ``yolo export model=<weights>.pt format=onnx``.
"""

import asyncio
from collections.abc import Sequence
from typing import Any

from specter.core.errors import ConfigurationError
from specter.core.settings import DetectorSettings
from specter.domain.vision import Detection, Frame
from specter.infrastructure.ml._ultralytics_common import detections_from_result, overrides


class OnnxDetector:
    model_version = "yolo11-onnx"

    def __init__(self, cfg: DetectorSettings) -> None:
        if not cfg.weights.endswith(".onnx"):
            raise ConfigurationError(
                f"OnnxDetector needs .onnx weights, got {cfg.weights!r} — export with "
                "`yolo export model=<weights>.pt format=onnx`"
            )
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

            self._model = YOLO(self._weights, task="detect")
        return self._model

    def predict_sync(self, frames: list[Frame]) -> list[list[Detection]]:
        """Synchronous batched forward pass — the seam ``BatchedDetector``'s
        ``MicroBatcher`` calls directly (via ``asyncio.to_thread``) to coalesce frames
        from every concurrently-running stream into one call.

        Unlike ``YoloDetector``, the device is never set via ``model.to(...)`` — an
        exported (ONNX/TensorRT/...) model isn't a ``torch.nn.Module`` and ultralytics
        rejects that call outright. ``device`` has to be passed into ``predict()``
        itself instead, where ``AutoBackend`` reads it to pick the onnxruntime
        execution provider (``CoreMLExecutionProvider`` for ``"mps"``, on first call)."""
        model = self._load()
        results = model.predict(
            [frame.image for frame in frames],
            verbose=False,
            device=self._device,
            **overrides(self._conf, self._iou),
        )
        return [detections_from_result(result) for result in results]
