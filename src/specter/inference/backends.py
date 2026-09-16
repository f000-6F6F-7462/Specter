"""The interface every inference runtime implements, and choosing a runtime from the settings."""

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from specter.config.settings import DetectorBackend, DetectorSettings

type Tensor = NDArray[np.float32]
# A unit-length vector, so the dot product of two embeddings is their cosine similarity.
type Embedding = NDArray[np.float32]


class InferenceSession(Protocol):
    """A model loaded into a runtime, ready to run."""

    def run(self, inputs: Tensor) -> list[list[Tensor]]:
        """Runs the model on a batch shaped (count, channels, height, width).

        Returns each input's outputs in the model's output order. Output shapes differ between
        runtimes, so every model wrapper reshapes the outputs it reads.
        """
        ...


class InferenceBackend(Protocol):
    """A runtime that loads models of one file format."""

    def load(self, model_files: Sequence[Path]) -> InferenceSession:
        """Loads a model from its files, listed in the order of the model manifest."""
        ...


def create_inference_backend(detector_settings: DetectorSettings) -> InferenceBackend:
    """Returns the runtime that the settings select.

    Each runtime is imported only when it is selected, because a device installs only its own.
    """
    match detector_settings.backend:
        case DetectorBackend.ONNXRUNTIME:
            from specter.inference.onnxruntime_backend import OnnxRuntimeBackend  # noqa: PLC0415

            return OnnxRuntimeBackend(detector_settings.onnxruntime_execution_providers)
        case DetectorBackend.NCNN:
            from specter.inference.ncnn_backend import NcnnBackend  # noqa: PLC0415

            return NcnnBackend()


def normalize_embedding(model_output: Tensor) -> Embedding | None:
    """Returns a model's embedding output scaled to unit length.

    Returns None for an output that has no direction, such as all zeros, or that holds non-finite
    values; scaling it would store NaN vectors that match nothing and break similarity math.
    """
    vector = np.asarray(model_output, dtype=np.float32).reshape(-1)
    length = float(np.linalg.norm(vector))
    if not np.isfinite(length) or length == 0.0:
        return None
    return vector / np.float32(length)
