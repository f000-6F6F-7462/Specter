"""Entry point of the detector process."""

import asyncio
import logging
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress

from nats.errors import Error as NatsError
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from specter.config.settings import DetectorBackend, Settings
from specter.core.errors import ConfigurationError
from specter.core.shutdown import complete_unless_shutdown
from specter.detector.batcher import DetectionBatcher
from specter.detector.enrollment import EnrollmentModels, EnrollmentService
from specter.detector.identification import IdentificationService
from specter.frame_transport.detector_requests import (
    DETECTION_REQUESTS_SUBJECT,
    DETECTOR_QUEUE_GROUP,
    IDENTIFICATION_REQUESTS_SUBJECT,
)
from specter.inference.appearance_embedder import AppearanceEmbedder
from specter.inference.backends import InferenceBackend, InferenceSession, create_inference_backend
from specter.inference.face_detector import FaceDetector
from specter.inference.model_store import (
    EMBEDDING_MODEL_ROLES_BY_MODALITY,
    ModelFormat,
    ModelRole,
    ModelSpecification,
    ModelStore,
    load_model_manifest,
)
from specter.inference.object_detector import ObjectDetector
from specter.messaging.client import JobWorker, MessageBus
from specter.messaging.streams import ENROLLMENT_JOBS_CONSUMER
from specter.storage.database import DatabaseThread, open_database
from specter.storage.vector_index import VectorIndex

logger = logging.getLogger(__name__)

BACKEND_BY_MODEL_FORMAT = {
    ModelFormat.ONNX: DetectorBackend.ONNXRUNTIME,
    ModelFormat.NCNN: DetectorBackend.NCNN,
}
MILLISECONDS_PER_SECOND = 1000.0
MODEL_THREAD_NAME = "specter-models"
ENROLLMENT_PREPARATION_RETRY_SECONDS = 5.0


async def run(settings: Settings, shutdown_requested: asyncio.Event) -> None:
    """Runs the detector until shutdown is requested."""
    message_bus = MessageBus(client_name="specter-detector")
    try:
        is_connected = await complete_unless_shutdown(
            message_bus.connect(settings.services.nats_url), shutdown_requested
        )
        if is_connected:
            await message_bus.declare_streams(settings.matching.cooldown_seconds)
            await serve_models(settings, message_bus, shutdown_requested)
    finally:
        await message_bus.close()
    logger.info("detector stopped")


async def serve_models(
    settings: Settings, message_bus: MessageBus, shutdown_requested: asyncio.Event
) -> None:
    """Answers detection and identification requests and enrolls images until shutdown.

    Raises:
        ConfigurationError: A model is missing, or its format does not suit the configured runtime.
    """
    manifest = load_model_manifest()
    model_store = ModelStore(settings.paths.models_directory, manifest)
    models_by_role = await asyncio.to_thread(
        model_store.prepare_profile_models, settings.device.hardware_profile
    )
    for role, model in models_by_role.items():
        if BACKEND_BY_MODEL_FORMAT[model.format] is not settings.detector.backend:
            raise ConfigurationError(
                f"the {settings.device.hardware_profile} profile runs its {role} model as "
                f"{model.format}, which the {settings.detector.backend} runtime cannot run"
            )
    backend = create_inference_backend(settings.detector)
    sessions_by_role = {
        role: await asyncio.to_thread(load_model_session, backend, model_store, model)
        for role, model in models_by_role.items()
    }
    enrollment_models = build_enrollment_models(models_by_role, sessions_by_role)
    batcher = DetectionBatcher(
        enrollment_models.object_detection_session,
        enrollment_models.object_detector,
        max_batch_size=settings.detector.max_batch_size,
        max_batch_delay_seconds=settings.detector.max_batch_delay_milliseconds
        / MILLISECONDS_PER_SECOND,
    )
    # Identification and enrollment take turns on one thread, so live cameras wait at most one
    # enrollment and never compete with it for the CPU.
    model_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=MODEL_THREAD_NAME)
    identification_service = IdentificationService(
        face_detection_session=enrollment_models.face_detection_session,
        face_detector=enrollment_models.face_detector,
        face_recognition_session=enrollment_models.face_recognition_session,
        appearance_session=enrollment_models.appearance_session,
        appearance_embedder=enrollment_models.appearance_embedder,
        model_executor=model_executor,
    )
    profile_model_ids = manifest.profiles[settings.device.hardware_profile]
    database_thread = DatabaseThread(open_database(settings.paths.database_file))
    vector_index = VectorIndex.connect(settings.services.qdrant_url)
    enrollment_service = EnrollmentService(
        models=enrollment_models,
        model_executor=model_executor,
        data_directory=settings.paths.data_directory,
        database_thread=database_thread,
        vector_index=vector_index,
        message_bus=message_bus,
        model_versions_by_modality={
            modality: manifest.embedding_version(profile_model_ids[role])
            for modality, role in EMBEDDING_MODEL_ROLES_BY_MODALITY.items()
        },
        embedding_sizes_by_modality={
            modality: embedding_size
            for modality, role in EMBEDDING_MODEL_ROLES_BY_MODALITY.items()
            if (embedding_size := models_by_role[role].embedding_size) is not None
        },
    )
    batching_task = asyncio.create_task(batcher.run())
    try:
        await message_bus.serve_requests(
            DETECTION_REQUESTS_SUBJECT, DETECTOR_QUEUE_GROUP, batcher.answer
        )
        await message_bus.serve_requests(
            IDENTIFICATION_REQUESTS_SUBJECT, DETECTOR_QUEUE_GROUP, identification_service.answer
        )
        logger.info(
            "detector started",
            extra={
                "backend": settings.detector.backend,
                "hardware_profile": settings.device.hardware_profile,
            },
        )
        await enroll_images(enrollment_service, message_bus, shutdown_requested)
    finally:
        batching_task.cancel()
        await asyncio.gather(batching_task, return_exceptions=True)
        batcher.close()
        await asyncio.to_thread(identification_service.close)
        model_executor.shutdown(wait=True)
        await vector_index.close()
        database_thread.close()


async def enroll_images(
    enrollment_service: EnrollmentService,
    message_bus: MessageBus,
    shutdown_requested: asyncio.Event,
) -> None:
    """Prepares enrollment once Qdrant can be reached, then enrolls images until shutdown."""
    while not shutdown_requested.is_set():
        try:
            await enrollment_service.prepare()
            break
        except (UnexpectedResponse, ResponseHandlingException, NatsError) as error:
            # Qdrant can start after the detector; live detection keeps running meanwhile.
            logger.warning("cannot prepare enrollment, retrying: %s", error)
        with suppress(TimeoutError):
            await asyncio.wait_for(shutdown_requested.wait(), ENROLLMENT_PREPARATION_RETRY_SECONDS)
    await JobWorker(message_bus, ENROLLMENT_JOBS_CONSUMER, enrollment_service.handle_job).run(
        shutdown_requested
    )


def build_enrollment_models(
    models_by_role: Mapping[ModelRole, ModelSpecification],
    sessions_by_role: Mapping[ModelRole, InferenceSession],
) -> EnrollmentModels:
    """Wraps the profile's loaded models for detection, identification and enrollment."""
    object_detection_model = models_by_role[ModelRole.OBJECT_DETECTION]
    face_detection_model = models_by_role[ModelRole.FACE_DETECTION]
    appearance_model = models_by_role[ModelRole.APPEARANCE]
    return EnrollmentModels(
        object_detection_session=sessions_by_role[ModelRole.OBJECT_DETECTION],
        object_detector=ObjectDetector(
            object_detection_model.input_width, object_detection_model.input_height
        ),
        face_detection_session=sessions_by_role[ModelRole.FACE_DETECTION],
        face_detector=FaceDetector(
            face_detection_model.input_width, face_detection_model.input_height
        ),
        face_recognition_session=sessions_by_role[ModelRole.FACE_RECOGNITION],
        appearance_session=sessions_by_role[ModelRole.APPEARANCE],
        appearance_embedder=AppearanceEmbedder(
            appearance_model.input_width, appearance_model.input_height
        ),
    )


def load_model_session(
    backend: InferenceBackend, model_store: ModelStore, model: ModelSpecification
) -> InferenceSession:
    """Loads a prepared model into the runtime."""
    return backend.load([model_store.resolve_file(model_file) for model_file in model.files])
