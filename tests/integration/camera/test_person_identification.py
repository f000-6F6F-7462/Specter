import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
import pytest
import supervision

from specter.camera.detector_client import DetectorClient
from specter.camera.person_identifier import ConfirmedMatch, PersonIdentifier
from specter.detector.batcher import DetectionBatcher
from specter.detector.identification import IdentificationService
from specter.entities.cameras import Camera
from specter.entities.geometry import BoundingBox
from specter.entities.targets import EmbeddingModality, TargetType
from specter.entities.watchlists import Watchlist, WatchlistKind
from specter.frame_transport.detector_requests import (
    DETECTION_REQUESTS_SUBJECT,
    DETECTOR_QUEUE_GROUP,
    IDENTIFICATION_REQUESTS_SUBJECT,
    IdentificationTask,
    PixelBox,
)
from specter.inference.appearance_embedder import AppearanceEmbedder
from specter.inference.backends import InferenceSession
from specter.inference.face_detector import FaceDetector
from specter.inference.model_store import load_model_manifest
from specter.inference.object_detector import ObjectDetector
from specter.inference.onnxruntime_backend import OnnxRuntimeBackend
from specter.messaging.message_bus import MessageBus
from specter.messaging.shared_state import MatchCooldownBucket
from specter.storage.vector_index import StoredEmbedding, VectorIndex
from specter.vision.frames import Frame, FrameImage
from specter.vision.tracking import ObjectTracker

pytestmark = pytest.mark.models

MODELS_DIRECTORY = Path(__file__).resolve().parents[3] / "models"
BATCH_DELAY_SECONDS = 0.005
COOLDOWN_SECONDS = 30.0
FRAME_INTERVAL_SECONDS = 0.5
FRAME_COUNT = 12
# The woman on the left of the group photo, as YOLO26 finds her.
ENROLLED_PERSON_BOX = PixelBox(x=0, y=232, width=310, height=649)
MINIMUM_BOX_OVERLAP_RATIO = 0.5


def read_group_photo() -> FrameImage:
    image = cv2.imread(str(MODELS_DIRECTORY / "test_images" / "faces.jpg"))
    if image is None:
        pytest.fail(f"test images are missing from {MODELS_DIRECTORY}: run make models")
    return np.asarray(image, dtype=np.uint8)


def load_onnx_session(model_id: str) -> InferenceSession:
    model = load_model_manifest().models[model_id]
    return OnnxRuntimeBackend(["CPUExecutionProvider"]).load(
        [MODELS_DIRECTORY / model_file.path for model_file in model.files]
    )


def build_detection_batcher() -> DetectionBatcher:
    model = load_model_manifest().models["yolo26s_640_onnx"]
    return DetectionBatcher(
        load_onnx_session("yolo26s_640_onnx"),
        ObjectDetector(model.input_width, model.input_height),
        max_batch_size=1,
        max_batch_delay_seconds=BATCH_DELAY_SECONDS,
    )


def build_identification_service(model_executor: ThreadPoolExecutor) -> IdentificationService:
    manifest = load_model_manifest()
    face_model = manifest.models["scrfd_10g_onnx"]
    appearance_model = manifest.models["osnet_x0_25_onnx"]
    return IdentificationService(
        face_detection_session=load_onnx_session("scrfd_10g_onnx"),
        face_detector=FaceDetector(face_model.input_width, face_model.input_height),
        face_recognition_session=load_onnx_session("arcface_r50_onnx"),
        appearance_session=load_onnx_session("osnet_x0_25_onnx"),
        appearance_embedder=AppearanceEmbedder(
            appearance_model.input_width, appearance_model.input_height
        ),
        model_executor=model_executor,
    )


def build_frame(camera_id: str, frame_index: int, image: FrameImage) -> Frame:
    return Frame(
        camera_id=camera_id,
        sequence_number=frame_index + 1,
        presentation_time_seconds=frame_index * FRAME_INTERVAL_SECONDS,
        captured_at=datetime.now(UTC),
        image=image,
    )


async def enroll_face(
    detector_client: DetectorClient,
    vector_index: VectorIndex,
    frame: Frame,
    *,
    owner_id: str,
    watchlist_id: str,
    target_id: str,
) -> None:
    await detector_client.detect(frame)
    results = await detector_client.identify(
        frame,
        [
            IdentificationTask(
                track_id=0, person_box=ENROLLED_PERSON_BOX, minimum_face_quality_score_ratio=0.0
            )
        ],
    )
    assert results
    face = results[0].face
    assert face is not None
    assert face.embedding is not None
    await vector_index.upsert_embedding(
        StoredEmbedding(
            owner_id=owner_id,
            watchlist_id=watchlist_id,
            target_id=target_id,
            reference_image_id=f"{target_id}_image",
            modality=EmbeddingModality.FACE,
            vector=face.embedding,
            model_version="arcface_r50_onnx",
        )
    )


async def test_enrolled_person_is_confirmed_once_when_camera_keeps_seeing_her(
    message_bus: MessageBus, vector_index: VectorIndex
) -> None:
    unique_suffix = time.time_ns()
    owner_id = f"owner_identification_{unique_suffix}"
    camera = Camera(
        id=f"camera_identification_{unique_suffix}",
        owner_id=owner_id,
        name="Group photo",
        source_url="rtsp://camera.invalid/stream",
        watchlist_ids=(f"watchlist_{unique_suffix}",),
    )
    watchlist = Watchlist(
        id=camera.watchlist_ids[0],
        owner_id=owner_id,
        name="Wanted",
        target_type=TargetType.PERSON,
        kind=WatchlistKind.BLACKLIST,
    )
    target_id = f"target_{unique_suffix}"
    group_photo = read_group_photo()
    detection_batcher = build_detection_batcher()
    model_executor = ThreadPoolExecutor(max_workers=1)
    identification_service = build_identification_service(model_executor)
    batching_task = asyncio.create_task(detection_batcher.run())
    detector_client = DetectorClient(camera.id, message_bus)
    confirmed_matches: list[ConfirmedMatch] = []
    try:
        await message_bus.serve_requests(
            DETECTION_REQUESTS_SUBJECT, DETECTOR_QUEUE_GROUP, detection_batcher.answer
        )
        await message_bus.serve_requests(
            IDENTIFICATION_REQUESTS_SUBJECT, DETECTOR_QUEUE_GROUP, identification_service.answer
        )
        await enroll_face(
            detector_client,
            vector_index,
            build_frame(camera.id, 0, group_photo),
            owner_id=owner_id,
            watchlist_id=watchlist.id,
            target_id=target_id,
        )
        person_identifier = PersonIdentifier(
            camera,
            detector_client,
            vector_index,
            await MatchCooldownBucket.open(message_bus),
            COOLDOWN_SECONDS,
        )
        person_identifier.configure([watchlist])
        tracker = ObjectTracker(expected_fps=1 / FRAME_INTERVAL_SECONDS)
        for frame_index in range(1, FRAME_COUNT + 1):
            frame = build_frame(camera.id, frame_index, group_photo)
            detections = await detector_client.detect(frame)
            assert detections is not None
            tracking_update = tracker.update(
                detections, frame.presentation_time_seconds, frame.captured_at
            )
            confirmed_matches.extend(
                await person_identifier.identify(
                    frame, tracking_update, frame.presentation_time_seconds
                )
            )
    finally:
        detector_client.close()
        batching_task.cancel()
        await asyncio.gather(batching_task, return_exceptions=True)
        detection_batcher.close()
        identification_service.close()
        model_executor.shutdown(wait=True)

    assert len(confirmed_matches) == 1
    match = confirmed_matches[0]
    assert (match.decision.target_id, match.modality) == (target_id, EmbeddingModality.FACE)
    enrolled_box = BoundingBox(
        x=ENROLLED_PERSON_BOX.x,
        y=ENROLLED_PERSON_BOX.y,
        width=ENROLLED_PERSON_BOX.width,
        height=ENROLLED_PERSON_BOX.height,
    )
    track_box = match.track.detection.bounding_box
    overlap_ratio = supervision.box_iou(
        [track_box.x, track_box.y, track_box.right, track_box.bottom],
        [enrolled_box.x, enrolled_box.y, enrolled_box.right, enrolled_box.bottom],
    )
    assert overlap_ratio > MINIMUM_BOX_OVERLAP_RATIO
