"""Entry point of the detector process."""

import asyncio
import logging

from specter.config.settings import DetectorBackend, Settings
from specter.core.errors import ConfigurationError
from specter.core.shutdown import complete_unless_shutdown
from specter.detector.batcher import DetectionBatcher
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
    ModelFormat,
    ModelRole,
    ModelSpecification,
    ModelStore,
    load_model_manifest,
)
from specter.inference.object_detector import ObjectDetector
from specter.messaging.client import MessageBus

logger = logging.getLogger(__name__)

BACKEND_BY_MODEL_FORMAT = {
    ModelFormat.ONNX: DetectorBackend.ONNXRUNTIME,
    ModelFormat.NCNN: DetectorBackend.NCNN,
}
MILLISECONDS_PER_SECOND = 1000.0


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
    """Answers the cameras' detection and identification requests until shutdown is requested.

    Raises:
        ConfigurationError: A model is missing, or its format does not suit the configured runtime.
    """
    model_store = ModelStore(settings.paths.models_directory, load_model_manifest())
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

    object_detection_model = models_by_role[ModelRole.OBJECT_DETECTION]
    batcher = DetectionBatcher(
        sessions_by_role[ModelRole.OBJECT_DETECTION],
        ObjectDetector(object_detection_model.input_width, object_detection_model.input_height),
        max_batch_size=settings.detector.max_batch_size,
        max_batch_delay_seconds=settings.detector.max_batch_delay_milliseconds
        / MILLISECONDS_PER_SECOND,
    )
    face_detection_model = models_by_role[ModelRole.FACE_DETECTION]
    appearance_model = models_by_role[ModelRole.APPEARANCE]
    identification_service = IdentificationService(
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
        await shutdown_requested.wait()
    finally:
        batching_task.cancel()
        await asyncio.gather(batching_task, return_exceptions=True)
        batcher.close()
        await asyncio.to_thread(identification_service.close)


def load_model_session(
    backend: InferenceBackend, model_store: ModelStore, model: ModelSpecification
) -> InferenceSession:
    """Loads a prepared model into the runtime."""
    return backend.load([model_store.resolve_file(model_file) for model_file in model.files])
