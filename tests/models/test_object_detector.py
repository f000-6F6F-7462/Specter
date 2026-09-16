from collections import Counter
from collections.abc import Callable

import numpy as np
import pytest

from specter.inference.backends import InferenceSession
from specter.inference.model_store import ModelManifest
from specter.inference.object_detector import ObjectDetector
from specter.vision.frames import FrameImage

pytestmark = pytest.mark.models


@pytest.mark.parametrize("model_id", ["yolo26s_640_onnx", "yolo26n_320_ncnn"])
def test_people_and_bus_are_found_when_street_image_is_detected(
    model_manifest: ModelManifest,
    load_model_session: Callable[[str], InferenceSession],
    street_image: FrameImage,
    model_id: str,
) -> None:
    model = model_manifest.models[model_id]
    object_detector = ObjectDetector(model.input_width, model.input_height)
    session = load_model_session(model_id)
    letterboxed_image, transform = object_detector.letterbox(street_image)

    outputs = session.run(object_detector.build_input_tensor([letterboxed_image]))[0]
    detections = object_detector.decode(outputs, transform)

    class_counts = Counter(detection.object_class for detection in detections)
    assert class_counts["person"] >= 3
    assert class_counts["bus"] >= 1
    image_height, image_width = street_image.shape[:2]
    assert all(
        detection.bounding_box.right <= image_width
        and detection.bounding_box.bottom <= image_height
        for detection in detections
    )


def test_batch_gives_the_same_detections_when_frames_run_together(
    model_manifest: ModelManifest,
    load_model_session: Callable[[str], InferenceSession],
    street_image: FrameImage,
) -> None:
    model = model_manifest.models["yolo26s_640_onnx"]
    object_detector = ObjectDetector(model.input_width, model.input_height)
    session = load_model_session("yolo26s_640_onnx")
    letterboxed_image, _ = object_detector.letterbox(street_image)
    single_input = object_detector.build_input_tensor([letterboxed_image])

    single_outputs = session.run(single_input)[0]
    batch_outputs = session.run(object_detector.build_input_tensor([letterboxed_image] * 2))

    assert len(batch_outputs) == 2
    np.testing.assert_allclose(batch_outputs[1][0], single_outputs[0], rtol=1e-3, atol=1e-3)
