"""Entry point of the detector process."""

import asyncio
import logging

from specter.config.settings import DetectorBackend, Settings
from specter.core.errors import ConfigurationError
from specter.core.shutdown import complete_unless_shutdown
from specter.detector.batcher import DetectionBatcher
from specter.frame_transport.detection_requests import (
    DETECTION_REQUESTS_SUBJECT,
    DETECTOR_QUEUE_GROUP,
)
from specter.inference.backends import create_inference_backend
from specter.inference.model_store import ModelFormat, ModelRole, ModelStore, load_model_manifest
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
            await serve_object_detection(settings, message_bus, shutdown_requested)
    finally:
        await message_bus.close()
    logger.info("detector stopped")


async def serve_object_detection(
    settings: Settings, message_bus: MessageBus, shutdown_requested: asyncio.Event
) -> None:
    """Answers the cameras' detection requests until shutdown is requested.

    Raises:
        ConfigurationError: A model is missing, or its format does not suit the configured runtime.
    """
    model_store = ModelStore(settings.paths.models_directory, load_model_manifest())
    models_by_role = await asyncio.to_thread(
        model_store.prepare_profile_models, settings.device.hardware_profile
    )
    object_detection_model = models_by_role[ModelRole.OBJECT_DETECTION]
    if BACKEND_BY_MODEL_FORMAT[object_detection_model.format] is not settings.detector.backend:
        raise ConfigurationError(
            f"the {settings.device.hardware_profile} profile uses {object_detection_model.format} "
            f"models, which the {settings.detector.backend} runtime cannot run"
        )
    backend = create_inference_backend(settings.detector)
    session = await asyncio.to_thread(
        backend.load,
        [model_store.resolve_file(model_file) for model_file in object_detection_model.files],
    )
    batcher = DetectionBatcher(
        session,
        ObjectDetector(object_detection_model.input_width, object_detection_model.input_height),
        max_batch_size=settings.detector.max_batch_size,
        max_batch_delay_seconds=settings.detector.max_batch_delay_milliseconds
        / MILLISECONDS_PER_SECOND,
    )
    batching_task = asyncio.create_task(batcher.run())
    try:
        await message_bus.serve_requests(
            DETECTION_REQUESTS_SUBJECT, DETECTOR_QUEUE_GROUP, batcher.answer
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
