import time
from collections.abc import Callable, Iterator

import numpy as np
import pytest

from specter.detector.identification import IdentificationService
from specter.frame_transport.detector_requests import (
    IdentificationRequest,
    IdentificationTask,
    PixelBox,
    SharedFrameReference,
)
from specter.frame_transport.shared_frames import SharedFrameWriter
from specter.inference.appearance_embedder import AppearanceEmbedder
from specter.inference.backends import InferenceSession
from specter.inference.face_detector import FaceDetector
from specter.inference.model_store import ModelManifest
from specter.vision.frames import FrameImage

pytestmark = pytest.mark.models

# People that YOLO26 finds in the group photo, largest faces first.
GROUP_PHOTO_PERSON_BOXES = (
    PixelBox(x=0, y=232, width=310, height=649),
    PixelBox(x=872, y=2, width=406, height=656),
    PixelBox(x=338, y=218, width=344, height=428),
    PixelBox(x=629, y=290, width=304, height=349),
)
EMBEDDING_SIZE = 512
# Model sets of the PC and Raspberry Pi profiles: face detection, face recognition, appearance.
PROFILE_IDENTIFICATION_MODELS = [
    ("scrfd_10g_onnx", "arcface_r50_onnx", "osnet_x0_25_onnx"),
    ("scrfd_500m_320_ncnn", "arcface_mobilefacenet_ncnn", "osnet_x0_25_ncnn"),
]


@pytest.fixture
def shared_group_photo(group_photo: FrameImage) -> Iterator[SharedFrameReference]:
    camera_id = f"camera_identification_{time.time_ns()}"
    frame_height, frame_width = group_photo.shape[:2]
    frame_writer = SharedFrameWriter(camera_id, frame_width, frame_height)
    frame_writer.write(group_photo, sequence_number=1)
    try:
        yield SharedFrameReference(
            camera_id=camera_id,
            shared_memory_name=frame_writer.name,
            frame_sequence_number=1,
            frame_width_pixels=frame_width,
            frame_height_pixels=frame_height,
        )
    finally:
        frame_writer.close()


def build_service(
    model_manifest: ModelManifest,
    load_model_session: Callable[[str], InferenceSession],
    model_ids: tuple[str, str, str],
) -> IdentificationService:
    face_detection_model_id, face_recognition_model_id, appearance_model_id = model_ids
    face_detection_model = model_manifest.models[face_detection_model_id]
    appearance_model = model_manifest.models[appearance_model_id]
    return IdentificationService(
        face_detection_session=load_model_session(face_detection_model_id),
        face_detector=FaceDetector(
            face_detection_model.input_width, face_detection_model.input_height
        ),
        face_recognition_session=load_model_session(face_recognition_model_id),
        appearance_session=load_model_session(appearance_model_id),
        appearance_embedder=AppearanceEmbedder(
            appearance_model.input_width, appearance_model.input_height
        ),
    )


@pytest.mark.parametrize("model_ids", PROFILE_IDENTIFICATION_MODELS)
def test_people_get_unit_length_embeddings_when_group_photo_is_identified(
    model_manifest: ModelManifest,
    load_model_session: Callable[[str], InferenceSession],
    shared_group_photo: SharedFrameReference,
    model_ids: tuple[str, str, str],
) -> None:
    service = build_service(model_manifest, load_model_session, model_ids)
    request = IdentificationRequest(
        frame=shared_group_photo,
        tasks=tuple(
            IdentificationTask(
                track_id=track_id,
                person_box=person_box,
                minimum_face_quality_score_ratio=0.0,
                embeds_appearance=True,
            )
            for track_id, person_box in enumerate(GROUP_PHOTO_PERSON_BOXES)
        ),
    )
    try:
        reply = service.identify(request)
    finally:
        service.close()

    face_embeddings = [
        np.array(result.face.embedding)
        for result in reply.results
        if result.face is not None and result.face.embedding is not None
    ]
    appearance_embeddings = [
        np.array(result.appearance_embedding)
        for result in reply.results
        if result.appearance_embedding is not None
    ]
    assert len(face_embeddings) >= 3
    assert len(appearance_embeddings) == len(GROUP_PHOTO_PERSON_BOXES)
    for embedding in (*face_embeddings, *appearance_embeddings):
        assert embedding.shape == (EMBEDDING_SIZE,)
        assert np.linalg.norm(embedding) == pytest.approx(1.0, abs=1e-4)
    # Different people must not look like the same face.
    assert float(face_embeddings[0] @ face_embeddings[1]) < 0.5


def test_face_is_not_embedded_when_it_cannot_beat_the_requested_score(
    model_manifest: ModelManifest,
    load_model_session: Callable[[str], InferenceSession],
    shared_group_photo: SharedFrameReference,
) -> None:
    service = build_service(model_manifest, load_model_session, PROFILE_IDENTIFICATION_MODELS[0])
    request = IdentificationRequest(
        frame=shared_group_photo,
        tasks=(
            IdentificationTask(
                track_id=0,
                person_box=GROUP_PHOTO_PERSON_BOXES[0],
                minimum_face_quality_score_ratio=1.0,
            ),
        ),
    )
    try:
        reply = service.identify(request)
    finally:
        service.close()

    face = reply.results[0].face
    assert face is not None
    assert face.quality_score_ratio > 0
    assert face.embedding is None
    assert reply.results[0].appearance_embedding is None


def test_reply_is_empty_when_camera_already_replaced_the_frame(
    model_manifest: ModelManifest,
    load_model_session: Callable[[str], InferenceSession],
    shared_group_photo: SharedFrameReference,
) -> None:
    service = build_service(model_manifest, load_model_session, PROFILE_IDENTIFICATION_MODELS[0])
    request = IdentificationRequest(
        frame=shared_group_photo.model_copy(update={"frame_sequence_number": 2}),
        tasks=(
            IdentificationTask(
                track_id=0, person_box=GROUP_PHOTO_PERSON_BOXES[0], embeds_appearance=True
            ),
        ),
    )
    try:
        reply = service.identify(request)
    finally:
        service.close()

    assert reply.results == ()
