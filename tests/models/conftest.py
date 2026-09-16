from collections.abc import Callable
from functools import partial
from pathlib import Path

import cv2
import numpy as np
import pytest

from specter.inference.backends import InferenceSession
from specter.inference.model_store import ModelFormat, ModelManifest, load_model_manifest
from specter.inference.ncnn_backend import NcnnBackend
from specter.inference.onnxruntime_backend import OnnxRuntimeBackend
from specter.vision.frames import FrameImage

MODELS_DIRECTORY = Path(__file__).resolve().parents[2] / "models"
CPU_EXECUTION_PROVIDERS = ["CPUExecutionProvider"]

type ModelSessionLoader = Callable[[str], InferenceSession]


@pytest.fixture(scope="session")
def model_manifest() -> ModelManifest:
    if not (MODELS_DIRECTORY / "test_images").is_dir():
        pytest.fail(f"model files are missing from {MODELS_DIRECTORY}: run make models")
    return load_model_manifest()


@pytest.fixture(scope="session")
def street_image() -> FrameImage:
    return read_test_image("bus.jpg")


@pytest.fixture(scope="session")
def group_photo() -> FrameImage:
    return read_test_image("faces.jpg")


def read_test_image(file_name: str) -> FrameImage:
    image = cv2.imread(str(MODELS_DIRECTORY / "test_images" / file_name))
    if image is None:
        pytest.fail(f"test image {file_name} is missing from {MODELS_DIRECTORY}: run make models")
    return np.asarray(image, dtype=np.uint8)


def load_session(model_manifest: ModelManifest, model_id: str) -> InferenceSession:
    model = model_manifest.models[model_id]
    model_files = [MODELS_DIRECTORY / model_file.path for model_file in model.files]
    if model.format is ModelFormat.NCNN:
        return NcnnBackend().load(model_files)
    return OnnxRuntimeBackend(CPU_EXECUTION_PROVIDERS).load(model_files)


@pytest.fixture(scope="session")
def load_model_session(model_manifest: ModelManifest) -> ModelSessionLoader:
    return partial(load_session, model_manifest)
