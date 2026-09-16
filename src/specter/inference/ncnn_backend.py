"""Runs NCNN models, which are the fastest option on ARM CPUs such as the Raspberry Pi's."""

import logging
import re
from collections.abc import Sequence
from pathlib import Path

import ncnn
import numpy as np

from specter.core.errors import ConfigurationError
from specter.inference.backends import Tensor

logger = logging.getLogger(__name__)

PARAMETER_FILE_SUFFIX = ".param"
WEIGHT_FILE_SUFFIX = ".bin"
BLOB_NUMBER_PATTERN = re.compile(r"(\d+)$")
NCNN_SUCCESS = 0


class NcnnSession:
    """An NCNN model, which runs one image at a time."""

    def __init__(self, network: ncnn.Net) -> None:
        self._network = network
        self._input_name: str = network.input_names()[0]
        # pnnx numbers the outputs in the source model's order, which NCNN does not list them in.
        self._output_names: list[str] = sorted(network.output_names(), key=self._blob_number)

    def run(self, inputs: Tensor) -> list[list[Tensor]]:
        """Runs the model on each input of the batch and returns each input's outputs."""
        return [self._run_one(model_input) for model_input in inputs]

    def _run_one(self, model_input: Tensor) -> list[Tensor]:
        with self._network.create_extractor() as extractor:
            extractor.input(self._input_name, ncnn.Mat(np.ascontiguousarray(model_input)))
            outputs: list[Tensor] = []
            for output_name in self._output_names:
                _, output = extractor.extract(output_name)
                # A leading batch dimension matches the outputs of ONNX Runtime.
                outputs.append(np.array(output, dtype=np.float32)[np.newaxis])
        return outputs

    @staticmethod
    def _blob_number(blob_name: str) -> int:
        number_match = BLOB_NUMBER_PATTERN.search(blob_name)
        return int(number_match.group(1)) if number_match else 0


class NcnnBackend:
    """Loads NCNN models from their parameter and weight files."""

    def load(self, model_files: Sequence[Path]) -> NcnnSession:
        """Loads the model's parameter file and weight file."""
        parameter_file = self._find_file(model_files, PARAMETER_FILE_SUFFIX)
        weight_file = self._find_file(model_files, WEIGHT_FILE_SUFFIX)
        network = ncnn.Net()
        if (
            network.load_param(str(parameter_file)) != NCNN_SUCCESS
            or network.load_model(str(weight_file)) != NCNN_SUCCESS
        ):
            raise ConfigurationError(f"cannot load NCNN model {parameter_file}")
        logger.info("loaded NCNN model", extra={"model_file": str(parameter_file)})
        return NcnnSession(network)

    @staticmethod
    def _find_file(model_files: Sequence[Path], suffix: str) -> Path:
        for model_file in model_files:
            if model_file.suffix == suffix:
                return model_file
        raise ConfigurationError(f"NCNN model files {list(model_files)} include no {suffix} file")
