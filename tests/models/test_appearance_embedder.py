from collections.abc import Callable

import pytest

from specter.inference.appearance_embedder import AppearanceEmbedder
from specter.inference.backends import InferenceSession
from specter.inference.model_store import ModelManifest
from specter.inference.object_detector import ObjectDetector
from specter.vision.frames import FrameImage

pytestmark = pytest.mark.models

CROP_SHIFT_PIXELS = 6


def crop_people(
    model_manifest: ModelManifest,
    load_model_session: Callable[[str], InferenceSession],
    image: FrameImage,
) -> list[tuple[int, int, int, int]]:
    model = model_manifest.models["yolo26s_640_onnx"]
    object_detector = ObjectDetector(model.input_width, model.input_height)
    letterboxed_image, transform = object_detector.letterbox(image)
    outputs = load_model_session("yolo26s_640_onnx").run(
        object_detector.build_input_tensor([letterboxed_image])
    )[0]
    return [
        (
            detection.bounding_box.x,
            detection.bounding_box.y,
            detection.bounding_box.right,
            detection.bounding_box.bottom,
        )
        for detection in object_detector.decode(outputs, transform)
        if detection.object_class == "person"
    ]


@pytest.mark.parametrize("model_id", ["osnet_x0_25_onnx", "osnet_x0_25_ncnn"])
def test_shifted_crop_is_closer_than_another_person_when_people_are_embedded(
    model_manifest: ModelManifest,
    load_model_session: Callable[[str], InferenceSession],
    street_image: FrameImage,
    model_id: str,
) -> None:
    model = model_manifest.models[model_id]
    appearance_embedder = AppearanceEmbedder(model.input_width, model.input_height)
    session = load_model_session(model_id)
    (left, top, right, bottom), (other_left, other_top, other_right, other_bottom) = crop_people(
        model_manifest, load_model_session, street_image
    )[:2]
    crops = [
        street_image[top:bottom, left:right],
        street_image[top + CROP_SHIFT_PIXELS : bottom, left + CROP_SHIFT_PIXELS : right],
        street_image[other_top:other_bottom, other_left:other_right],
    ]

    all_outputs = session.run(appearance_embedder.build_input_tensor(crops))
    person, shifted_person, other_person = (
        AppearanceEmbedder.read_embedding(outputs) for outputs in all_outputs
    )

    assert float(person @ shifted_person) > float(person @ other_person)


def test_ncnn_conversion_gives_the_same_embedding_when_compared_with_onnx(
    model_manifest: ModelManifest,
    load_model_session: Callable[[str], InferenceSession],
    street_image: FrameImage,
) -> None:
    model = model_manifest.models["osnet_x0_25_onnx"]
    appearance_embedder = AppearanceEmbedder(model.input_width, model.input_height)
    left, top, right, bottom = crop_people(model_manifest, load_model_session, street_image)[0]
    model_input = appearance_embedder.build_input_tensor([street_image[top:bottom, left:right]])

    onnx_embedding = AppearanceEmbedder.read_embedding(
        load_model_session("osnet_x0_25_onnx").run(model_input)[0]
    )
    ncnn_embedding = AppearanceEmbedder.read_embedding(
        load_model_session("osnet_x0_25_ncnn").run(model_input)[0]
    )

    assert float(onnx_embedding @ ncnn_embedding) > 0.99
