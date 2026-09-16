"""Runs ONNX models with ONNX Runtime, on a GPU when one is available and on the CPU otherwise."""

import logging
import os
from collections.abc import Sequence
from pathlib import Path

# ONNX Runtime starts a telemetry uploader to Microsoft when it is imported, unless this is set
# first. A security device must not send data out, and the uploader's thread can also crash the
# process as it exits; disabling telemetry after the import leaves the uploader running.
os.environ["ORT_DISABLE_TELEMETRY"] = "1"

import onnxruntime  # isort: skip

from specter.core.errors import ConfigurationError
from specter.inference.backends import Tensor

logger = logging.getLogger(__name__)


class OnnxRuntimeSession:
    """An ONNX model, which runs a whole batch in one call when its batch dimension is dynamic."""

    def __init__(self, session: onnxruntime.InferenceSession) -> None:
        self._session = session
        model_input = session.get_inputs()[0]
        self._input_name: str = model_input.name
        # A fixed batch dimension is a number; a dynamic one is a name.
        self._is_batch_dynamic = not isinstance(model_input.shape[0], int)

    def run(self, inputs: Tensor) -> list[list[Tensor]]:
        """Runs the model on the batch and returns each input's outputs."""
        if self._is_batch_dynamic:
            batch_outputs: list[Tensor] = self._session.run(None, {self._input_name: inputs})
            return [
                [output[index : index + 1] for output in batch_outputs]
                for index in range(len(inputs))
            ]
        return [
            list(self._session.run(None, {self._input_name: inputs[index : index + 1]}))
            for index in range(len(inputs))
        ]


class OnnxRuntimeBackend:
    """Loads ONNX models onto the execution providers that are installed, in preference order."""

    def __init__(self, execution_providers: Sequence[str]) -> None:
        available_providers = onnxruntime.get_available_providers()
        # Keeping only installed providers lets one preset serve devices with and without a GPU.
        self._execution_providers = [
            provider for provider in execution_providers if provider in available_providers
        ]
        if not self._execution_providers:
            raise ConfigurationError(
                f"none of the execution providers {list(execution_providers)} is installed; "
                f"installed: {available_providers}"
            )

    def load(self, model_files: Sequence[Path]) -> OnnxRuntimeSession:
        """Loads the ONNX model file."""
        session_options = onnxruntime.SessionOptions()
        session_options.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
        session = onnxruntime.InferenceSession(
            str(model_files[0]), sess_options=session_options, providers=self._execution_providers
        )
        logger.info(
            "loaded ONNX model",
            extra={"model_file": str(model_files[0]), "providers": session.get_providers()},
        )
        return OnnxRuntimeSession(session)
