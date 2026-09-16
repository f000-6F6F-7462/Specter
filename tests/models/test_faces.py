from collections.abc import Callable

import cv2
import numpy as np
import pytest

from specter.inference.backends import InferenceSession
from specter.inference.face_detector import FaceDetector
from specter.inference.face_embedder import FaceEmbedder
from specter.inference.model_store import ModelManifest
from specter.vision.detections import FaceDetection
from specter.vision.frames import FrameImage

pytestmark = pytest.mark.models

RESCALE_RATIO = 0.8
# Detector and recognizer model pairs of the PC and Raspberry Pi profiles.
PROFILE_FACE_MODELS = [
    ("scrfd_10g_onnx", "arcface_r50_onnx"),
    ("scrfd_500m_320_ncnn", "arcface_mobilefacenet_ncnn"),
]


def detect_faces(
    model_manifest: ModelManifest,
    load_model_session: Callable[[str], InferenceSession],
    model_id: str,
    image: FrameImage,
) -> list[FaceDetection]:
    model = model_manifest.models[model_id]
    face_detector = FaceDetector(model.input_width, model.input_height)
    model_input, scale = face_detector.prepare(image)
    outputs = load_model_session(model_id).run(model_input)[0]
    return face_detector.decode(outputs, scale)


def embed_faces(
    load_model_session: Callable[[str], InferenceSession],
    model_id: str,
    image: FrameImage,
    faces: list[FaceDetection],
) -> list[np.ndarray]:
    aligned_faces = [FaceEmbedder.align(image, face) for face in faces]
    all_outputs = load_model_session(model_id).run(FaceEmbedder.build_input_tensor(aligned_faces))
    return [FaceEmbedder.read_embedding(outputs) for outputs in all_outputs]


def find_nearest_face(faces: list[FaceDetection], center_x: float, center_y: float) -> int:
    distances = [
        (face.bounding_box.x + face.bounding_box.width / 2 - center_x) ** 2
        + (face.bounding_box.y + face.bounding_box.height / 2 - center_y) ** 2
        for face in faces
    ]
    return int(np.argmin(distances))


@pytest.mark.parametrize("detector_model_id", ["scrfd_10g_onnx", "scrfd_500m_320_ncnn"])
def test_every_face_has_landmarks_inside_its_box_when_group_photo_is_detected(
    model_manifest: ModelManifest,
    load_model_session: Callable[[str], InferenceSession],
    group_photo: FrameImage,
    detector_model_id: str,
) -> None:
    faces = detect_faces(model_manifest, load_model_session, detector_model_id, group_photo)

    assert len(faces) >= 4
    for face in faces:
        box = face.bounding_box
        assert all(box.x <= x <= box.right and box.y <= y <= box.bottom for x, y in face.landmarks)


@pytest.mark.parametrize(("detector_model_id", "recognizer_model_id"), PROFILE_FACE_MODELS)
def test_same_person_is_closer_than_anyone_else_when_photo_is_rescaled(
    model_manifest: ModelManifest,
    load_model_session: Callable[[str], InferenceSession],
    group_photo: FrameImage,
    detector_model_id: str,
    recognizer_model_id: str,
) -> None:
    rescaled_photo = np.asarray(
        cv2.resize(group_photo, None, fx=RESCALE_RATIO, fy=RESCALE_RATIO), dtype=np.uint8
    )
    faces = detect_faces(model_manifest, load_model_session, detector_model_id, group_photo)
    rescaled_faces = detect_faces(
        model_manifest, load_model_session, detector_model_id, rescaled_photo
    )
    embeddings = embed_faces(load_model_session, recognizer_model_id, group_photo, faces)
    rescaled_embeddings = embed_faces(
        load_model_session, recognizer_model_id, rescaled_photo, rescaled_faces
    )

    for face_index, face in enumerate(faces):
        center_x = (face.bounding_box.x + face.bounding_box.width / 2) * RESCALE_RATIO
        center_y = (face.bounding_box.y + face.bounding_box.height / 2) * RESCALE_RATIO
        same_person_index = find_nearest_face(rescaled_faces, center_x, center_y)
        similarities = [
            float(embeddings[face_index] @ rescaled_embedding)
            for rescaled_embedding in rescaled_embeddings
        ]
        assert int(np.argmax(similarities)) == same_person_index


def test_ncnn_conversion_finds_the_same_faces_when_compared_with_onnx(
    model_manifest: ModelManifest,
    load_model_session: Callable[[str], InferenceSession],
    group_photo: FrameImage,
) -> None:
    onnx_faces = detect_faces(model_manifest, load_model_session, "scrfd_500m_onnx", group_photo)
    ncnn_faces = detect_faces(
        model_manifest, load_model_session, "scrfd_500m_320_ncnn", group_photo
    )

    assert len(ncnn_faces) == len(onnx_faces)
    for onnx_face in onnx_faces:
        nearest_face = ncnn_faces[
            find_nearest_face(
                ncnn_faces,
                onnx_face.bounding_box.x + onnx_face.bounding_box.width / 2,
                onnx_face.bounding_box.y + onnx_face.bounding_box.height / 2,
            )
        ]
        assert nearest_face.bounding_box.iou(onnx_face.bounding_box) > 0.9


def test_ncnn_conversion_gives_the_same_embedding_when_compared_with_onnx(
    model_manifest: ModelManifest,
    load_model_session: Callable[[str], InferenceSession],
    group_photo: FrameImage,
) -> None:
    faces = detect_faces(model_manifest, load_model_session, "scrfd_10g_onnx", group_photo)[:1]

    onnx_embedding = embed_faces(
        load_model_session, "arcface_mobilefacenet_onnx", group_photo, faces
    )
    ncnn_embedding = embed_faces(
        load_model_session, "arcface_mobilefacenet_ncnn", group_photo, faces
    )

    assert float(onnx_embedding[0] @ ncnn_embedding[0]) > 0.99
