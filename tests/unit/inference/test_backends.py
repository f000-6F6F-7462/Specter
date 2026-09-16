import os

import numpy as np
import pytest

from specter.inference import onnxruntime_backend
from specter.inference.backends import normalize_embedding


def test_embedding_is_scaled_to_unit_length() -> None:
    embedding = normalize_embedding(np.array([[3.0, 4.0]], dtype=np.float32))

    assert embedding is not None
    np.testing.assert_allclose(embedding, [0.6, 0.8])


@pytest.mark.parametrize("output_value", [0.0, np.nan, np.inf])
def test_embedding_is_missing_when_the_output_has_no_direction(output_value: float) -> None:
    assert normalize_embedding(np.full((1, 4), output_value, dtype=np.float32)) is None


def test_onnx_runtime_telemetry_is_disabled_when_the_backend_is_imported() -> None:
    assert onnxruntime_backend.OnnxRuntimeBackend is not None
    assert os.environ["ORT_DISABLE_TELEMETRY"] == "1"
