from collections.abc import Callable

import numpy as np
import pytest

from specter.detector.enrollment import EnrollmentModels, embed_appearance, embed_face
from specter.entities.targets import RejectionReason
from specter.inference.appearance_embedder import AppearanceEmbedder
from specter.inference.backends import InferenceSession
from specter.inference.face_detector import FaceDetector
from specter.inference.model_store import ModelManifest
from specter.inference.object_detector import ObjectDetector
from specter.vision.frames import FrameImage

pytestmark = pytest.mark.models

EMBEDDING_SIZE = 512
# The woman on the left of the group photo, cropped so that she is the only person in it.
SINGLE_PERSON_REGION = (slice(232, 886), slice(0, 300))
BLANK_IMAGE_SIZE_PIXELS = 640
BLANK_IMAGE_INTENSITY = 128


@pytest.fixture(scope="module")
def enrollment_models(
    model_manifest: ModelManifest, load_model_session: Callable[[str], InferenceSession]
) -> EnrollmentModels:
    object_model = model_manifest.models["yolo26s_640_onnx"]
    face_model = model_manifest.models["scrfd_10g_onnx"]
    appearance_model = model_manifest.models["osnet_x0_25_onnx"]
    return EnrollmentModels(
        object_detection_session=load_model_session("yolo26s_640_onnx"),
        object_detector=ObjectDetector(object_model.input_width, object_model.input_height),
        face_detection_session=load_model_session("scrfd_10g_onnx"),
        face_detector=FaceDetector(face_model.input_width, face_model.input_height),
        face_recognition_session=load_model_session("arcface_r50_onnx"),
        appearance_session=load_model_session("osnet_x0_25_onnx"),
        appearance_embedder=AppearanceEmbedder(
            appearance_model.input_width, appearance_model.input_height
        ),
    )


def crop_single_person(group_photo: FrameImage) -> FrameImage:
    return np.ascontiguousarray(group_photo[SINGLE_PERSON_REGION])


def blank_image() -> FrameImage:
    return np.full(
        (BLANK_IMAGE_SIZE_PIXELS, BLANK_IMAGE_SIZE_PIXELS, 3), BLANK_IMAGE_INTENSITY, np.uint8
    )


def test_single_face_is_embedded_with_its_quality_when_photo_shows_one_person(
    enrollment_models: EnrollmentModels, group_photo: FrameImage
) -> None:
    outcome = embed_face(enrollment_models, crop_single_person(group_photo))

    assert outcome.rejection_reason is None
    assert outcome.quality is not None
    assert outcome.embedding is not None
    assert outcome.embedding.shape == (EMBEDDING_SIZE,)


def test_face_is_rejected_when_photo_shows_several_people(
    enrollment_models: EnrollmentModels, group_photo: FrameImage
) -> None:
    outcome = embed_face(enrollment_models, group_photo)

    assert outcome.rejection_reason is RejectionReason.MULTIPLE_FACES
    assert outcome.embedding is None


def test_face_is_rejected_when_photo_shows_no_face(enrollment_models: EnrollmentModels) -> None:
    assert embed_face(enrollment_models, blank_image()).rejection_reason is (
        RejectionReason.NO_FACE_DETECTED
    )


def test_appearance_is_embedded_when_photo_shows_one_person(
    enrollment_models: EnrollmentModels, group_photo: FrameImage
) -> None:
    outcome = embed_appearance(enrollment_models, crop_single_person(group_photo))

    assert outcome.rejection_reason is None
    assert outcome.embedding is not None
    assert outcome.embedding.shape == (EMBEDDING_SIZE,)


def test_appearance_is_rejected_when_photo_shows_several_people(
    enrollment_models: EnrollmentModels, street_image: FrameImage
) -> None:
    assert embed_appearance(enrollment_models, street_image).rejection_reason is (
        RejectionReason.MULTIPLE_PEOPLE
    )


def test_appearance_is_rejected_when_photo_shows_nobody(
    enrollment_models: EnrollmentModels,
) -> None:
    assert embed_appearance(enrollment_models, blank_image()).rejection_reason is (
        RejectionReason.NO_PERSON_DETECTED
    )
