import asyncio
import time
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path

import cv2
import numpy as np
import pytest

from specter.detector.enrollment import EnrollmentModels, EnrollmentService, embed_face
from specter.entities.targets import EmbeddingModality, ImageStatus, Target, TargetType
from specter.entities.watchlists import Watchlist
from specter.inference.appearance_embedder import AppearanceEmbedder
from specter.inference.backends import InferenceSession
from specter.inference.face_detector import FaceDetector
from specter.inference.model_store import load_model_manifest
from specter.inference.object_detector import ObjectDetector
from specter.inference.onnxruntime_backend import OnnxRuntimeBackend
from specter.messaging.client import JobWorker, MessageBus
from specter.messaging.streams import ENROLLMENT_JOBS_CONSUMER
from specter.storage.database import DatabaseThread, open_database
from specter.storage.migrate import apply_migrations
from specter.storage.targets import get_target, save_target
from specter.storage.vector_index import VectorIndex
from specter.storage.watchlists import save_watchlist

pytestmark = pytest.mark.models

MODELS_DIRECTORY = Path(__file__).resolve().parents[3] / "models"
EMBEDDING_SIZE = 512
ENROLLMENT_TIMEOUT_SECONDS = 30.0
POLL_INTERVAL_SECONDS = 0.1
WORKER_STOP_TIMEOUT_SECONDS = 5.0
# The woman on the left of the group photo, cropped so that she is the only person in it.
SINGLE_PERSON_REGION = (slice(232, 886), slice(0, 300))
REFERENCE_IMAGE_PATH = "reference_images/jane.png"


@dataclass(frozen=True, slots=True)
class EnrollmentScene:
    data_directory: Path
    database_thread: DatabaseThread
    owner_id: str
    watchlist_id: str
    target_id: str
    reference_image_id: str


def load_onnx_session(model_id: str) -> InferenceSession:
    model = load_model_manifest().models[model_id]
    return OnnxRuntimeBackend(["CPUExecutionProvider"]).load(
        [MODELS_DIRECTORY / model_file.path for model_file in model.files]
    )


def build_enrollment_models() -> EnrollmentModels:
    manifest = load_model_manifest()
    object_model = manifest.models["yolo26s_640_onnx"]
    face_model = manifest.models["scrfd_10g_onnx"]
    appearance_model = manifest.models["osnet_x0_25_onnx"]
    return EnrollmentModels(
        object_detection_session=load_onnx_session("yolo26s_640_onnx"),
        object_detector=ObjectDetector(object_model.input_width, object_model.input_height),
        face_detection_session=load_onnx_session("scrfd_10g_onnx"),
        face_detector=FaceDetector(face_model.input_width, face_model.input_height),
        face_recognition_session=load_onnx_session("arcface_r50_onnx"),
        appearance_session=load_onnx_session("osnet_x0_25_onnx"),
        appearance_embedder=AppearanceEmbedder(
            appearance_model.input_width, appearance_model.input_height
        ),
    )


@pytest.fixture
async def enrollment_scene(tmp_path: Path) -> AsyncIterator[EnrollmentScene]:
    group_photo = cv2.imread(str(MODELS_DIRECTORY / "test_images" / "faces.jpg"))
    if group_photo is None:
        pytest.fail(f"test images are missing from {MODELS_DIRECTORY}: run make models")
    data_directory = tmp_path / "data"
    (data_directory / "reference_images").mkdir(parents=True)
    cv2.imwrite(str(data_directory / REFERENCE_IMAGE_PATH), group_photo[SINGLE_PERSON_REGION])
    database = open_database(tmp_path / "specter.sqlite3")
    apply_migrations(database)
    unique_suffix = time.time_ns()
    scene = EnrollmentScene(
        data_directory=data_directory,
        database_thread=DatabaseThread(database),
        owner_id=f"owner_enrollment_{unique_suffix}",
        watchlist_id=f"watchlist_enrollment_{unique_suffix}",
        target_id=f"target_enrollment_{unique_suffix}",
        reference_image_id=f"image_enrollment_{unique_suffix}",
    )
    await asyncio.get_running_loop().run_in_executor(
        scene.database_thread.executor, partial(save_scene_target, scene)
    )
    yield scene
    scene.database_thread.close()


@pytest.fixture
def model_executor() -> Iterator[ThreadPoolExecutor]:
    executor = ThreadPoolExecutor(max_workers=1)
    yield executor
    executor.shutdown(wait=True)


def save_scene_target(scene: EnrollmentScene) -> None:
    save_watchlist(
        Watchlist(
            id=scene.watchlist_id,
            owner_id=scene.owner_id,
            name="Wanted",
            target_type=TargetType.PERSON,
        )
    )
    target = Target(
        id=scene.target_id,
        watchlist_id=scene.watchlist_id,
        label="Jane",
        target_type=TargetType.PERSON,
    )
    save_target(
        target.with_reference_image(
            target.create_reference_image(scene.reference_image_id, REFERENCE_IMAGE_PATH)
        )
    )


def build_service(
    scene: EnrollmentScene,
    models: EnrollmentModels,
    model_executor: ThreadPoolExecutor,
    message_bus: MessageBus,
    vector_index: VectorIndex,
    face_model_version: str,
) -> EnrollmentService:
    return EnrollmentService(
        models=models,
        model_executor=model_executor,
        data_directory=scene.data_directory,
        database_thread=scene.database_thread,
        vector_index=vector_index,
        message_bus=message_bus,
        model_versions_by_modality={
            EmbeddingModality.FACE: face_model_version,
            EmbeddingModality.APPEARANCE: "osnet_x0_25_onnx",
        },
        embedding_sizes_by_modality=dict.fromkeys(EmbeddingModality, EMBEDDING_SIZE),
    )


async def read_face_model_versions_until_enrolled(
    scene: EnrollmentScene, expected_face_model_version: str
) -> dict[EmbeddingModality, tuple[ImageStatus, str | None]]:
    deadline = time.monotonic() + ENROLLMENT_TIMEOUT_SECONDS
    while True:
        target = await asyncio.get_running_loop().run_in_executor(
            scene.database_thread.executor, partial(get_target, scene.target_id)
        )
        states = {
            embedding.modality: (embedding.status, embedding.model_version)
            for embedding in target.reference_images[0].embeddings
        }
        is_enrolled = ImageStatus.PENDING not in {status for status, _ in states.values()}
        if is_enrolled and states[EmbeddingModality.FACE][1] == expected_face_model_version:
            return states
        if time.monotonic() > deadline:
            pytest.fail(f"enrollment did not finish in time: {states}")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def enroll(
    service: EnrollmentService,
    message_bus: MessageBus,
    scene: EnrollmentScene,
    expected_face_model_version: str,
) -> dict[EmbeddingModality, tuple[ImageStatus, str | None]]:
    await service.prepare()
    shutdown_requested = asyncio.Event()
    worker_task = asyncio.create_task(
        JobWorker(message_bus, ENROLLMENT_JOBS_CONSUMER, service.handle_job).run(shutdown_requested)
    )
    try:
        return await read_face_model_versions_until_enrolled(scene, expected_face_model_version)
    finally:
        shutdown_requested.set()
        await asyncio.wait_for(worker_task, WORKER_STOP_TIMEOUT_SECONDS)


async def test_person_is_enrolled_and_enrolled_again_when_the_face_model_changes(
    message_bus: MessageBus,
    vector_index: VectorIndex,
    enrollment_scene: EnrollmentScene,
    model_executor: ThreadPoolExecutor,
) -> None:
    models = build_enrollment_models()

    first_states = await enroll(
        build_service(
            enrollment_scene, models, model_executor, message_bus, vector_index, "arcface_r50_onnx"
        ),
        message_bus,
        enrollment_scene,
        "arcface_r50_onnx",
    )
    reenrolled_states = await enroll(
        build_service(
            enrollment_scene, models, model_executor, message_bus, vector_index, "arcface_next"
        ),
        message_bus,
        enrollment_scene,
        "arcface_next",
    )
    reference_image = cv2.imread(str(enrollment_scene.data_directory / REFERENCE_IMAGE_PATH))
    face_embedding = embed_face(models, np.asarray(reference_image, dtype=np.uint8)).embedding
    assert face_embedding is not None
    candidates = await vector_index.search(
        EmbeddingModality.FACE,
        face_embedding.tolist(),
        owner_id=enrollment_scene.owner_id,
        watchlist_ids=[enrollment_scene.watchlist_id],
        limit=5,
    )

    assert first_states == {
        EmbeddingModality.FACE: (ImageStatus.EMBEDDED, "arcface_r50_onnx"),
        EmbeddingModality.APPEARANCE: (ImageStatus.EMBEDDED, "osnet_x0_25_onnx"),
    }
    assert reenrolled_states[EmbeddingModality.FACE] == (ImageStatus.EMBEDDED, "arcface_next")
    assert [candidate.target_id for candidate in candidates] == [enrollment_scene.target_id]
    assert candidates[0].similarity_ratio == pytest.approx(1.0, abs=1e-3)
