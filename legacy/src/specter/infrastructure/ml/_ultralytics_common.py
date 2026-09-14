"""Shared pre/post-processing for the two Ultralytics-backed detectors
(:class:`~specter.infrastructure.ml.yolo_detector.YoloDetector` — native PyTorch/MPS —
and :class:`~specter.infrastructure.ml.onnx_detector.OnnxDetector` — ONNX Runtime with
CoreML EP). They differ only in which runtime ``ultralytics.YOLO`` dispatches to; the
result shape and box format are identical either way.
"""

from typing import Any

from specter.domain.vision import BBox, Detection


def overrides(conf: float | None, iou: float | None) -> dict[str, float]:
    """Only the thresholds someone actually set — omitted keys keep the model's own
    defaults instead of us guessing at a copy of them."""
    values = {"conf": conf, "iou": iou}
    return {name: value for name, value in values.items() if value is not None}


def detections_from_result(result: Any) -> list[Detection]:
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return []
    names = result.names
    detections: list[Detection] = []
    for xyxy, conf, cls_id in zip(
        boxes.xyxy.tolist(), boxes.conf.tolist(), boxes.cls.tolist(), strict=True
    ):
        x1, y1, x2, y2 = (int(round(v)) for v in xyxy)
        detections.append(
            Detection(
                cls=str(names[int(cls_id)]),
                confidence=float(conf),
                bbox=BBox(x=x1, y=y1, w=max(x2 - x1, 1), h=max(y2 - y1, 1)),
            )
        )
    return detections
