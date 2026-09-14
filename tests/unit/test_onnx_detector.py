"""OnnxDetector's own logic (weights validation) — no ultralytics import needed since
the model only loads lazily on first `.detect()` call."""

import pytest

from specter.core.errors import ConfigurationError
from specter.core.settings import DetectorSettings
from specter.infrastructure.ml.onnx_detector import OnnxDetector


def test_accepts_onnx_weights() -> None:
    detector = OnnxDetector(DetectorSettings(impl="onnx", weights="yolo11m.onnx"))
    assert detector.model_version == "yolo11-onnx"


def test_rejects_pt_weights() -> None:
    with pytest.raises(ConfigurationError, match=r"\.onnx"):
        OnnxDetector(DetectorSettings(impl="onnx", weights="yolo11m.pt"))


def test_rejects_weights_with_no_extension() -> None:
    with pytest.raises(ConfigurationError):
        OnnxDetector(DetectorSettings(impl="onnx", weights="yolo11m"))
