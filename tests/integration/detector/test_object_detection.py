import asyncio
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
import pytest

from specter.camera.detection_client import DetectionClient
from specter.detector.batcher import DetectionBatcher
from specter.frame_transport.detection_requests import (
    DETECTION_REQUESTS_SUBJECT,
    DETECTOR_QUEUE_GROUP,
)
from specter.inference.model_store import load_model_manifest
from specter.inference.object_detector import ObjectDetector
from specter.inference.onnxruntime_backend import OnnxRuntimeBackend
from specter.messaging.client import MessageBus
from specter.vision.frames import Frame

pytestmark = pytest.mark.models

MODELS_DIRECTORY = Path(__file__).resolve().parents[3] / "models"
MODEL_ID = "yolo26s_640_onnx"
BATCH_DELAY_SECONDS = 0.005


async def test_people_and_bus_are_found_when_camera_sends_a_frame_to_the_detector(
    message_bus: MessageBus,
) -> None:
    street_image = cv2.imread(str(MODELS_DIRECTORY / "test_images" / "bus.jpg"))
    if street_image is None:
        pytest.fail(f"test images are missing from {MODELS_DIRECTORY}: run make models")
    model = load_model_manifest().models[MODEL_ID]
    session = OnnxRuntimeBackend(["CPUExecutionProvider"]).load(
        [MODELS_DIRECTORY / model_file.path for model_file in model.files]
    )
    batcher = DetectionBatcher(
        session,
        ObjectDetector(model.input_width, model.input_height),
        max_batch_size=4,
        max_batch_delay_seconds=BATCH_DELAY_SECONDS,
    )
    batching_task = asyncio.create_task(batcher.run())
    detection_client = DetectionClient(
        "camera_detection_test", message_bus, model.input_width, model.input_height
    )
    # The stream reader hands over frames already scaled to fit the model input.
    scale = min(
        model.input_width / street_image.shape[1], model.input_height / street_image.shape[0]
    )
    scaled_image = np.asarray(cv2.resize(street_image, None, fx=scale, fy=scale), dtype=np.uint8)
    frame = Frame(
        camera_id="camera_detection_test",
        sequence_number=1,
        presentation_time_seconds=0.0,
        captured_at=datetime.now(UTC),
        image=scaled_image,
    )
    try:
        await message_bus.serve_requests(
            DETECTION_REQUESTS_SUBJECT, DETECTOR_QUEUE_GROUP, batcher.answer
        )
        detections = await detection_client.detect(frame)
    finally:
        detection_client.close()
        batching_task.cancel()
        await asyncio.gather(batching_task, return_exceptions=True)
        batcher.close()

    assert detections is not None
    class_counts = Counter(detection.object_class for detection in detections)
    assert class_counts["person"] >= 3
    assert class_counts["bus"] >= 1
    assert detection_client.latency_percentile_milliseconds is not None
