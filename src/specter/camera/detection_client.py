"""A camera process's link to the detector: its shared frame region and its detection requests."""

import logging
import time
from collections import deque

import numpy as np
from nats.errors import Error as NatsError

from specter.entities.geometry import BoundingBox
from specter.frame_transport.detection_requests import (
    DETECTION_REQUESTS_SUBJECT,
    DetectionReply,
    DetectionRequest,
)
from specter.frame_transport.shared_frames import SharedFrameWriter
from specter.messaging.client import MessageBus
from specter.vision.detections import Detection
from specter.vision.frames import Frame

logger = logging.getLogger(__name__)

DETECTION_TIMEOUT_SECONDS = 2.0
# The gray that YOLO models are trained to see around a letterboxed image.
LETTERBOX_PADDING_INTENSITY = 114
LATENCY_WINDOW_SIZE = 20
LATENCY_PERCENTILE = 95
MILLISECONDS_PER_SECOND = 1000.0


class DetectionClient:
    """Sends a camera's frames to the detector one at a time and returns the objects found.

    Frames arrive already scaled to fit the model's input, so the client only pads them to the
    input's full size before writing them to the camera's shared memory region.
    """

    def __init__(
        self, camera_id: str, message_bus: MessageBus, input_width: int, input_height: int
    ) -> None:
        self._camera_id = camera_id
        self._message_bus = message_bus
        self._input_width = input_width
        self._input_height = input_height
        self._frame_writer = SharedFrameWriter(camera_id, input_width, input_height)
        self._letterboxed_image = np.empty((input_height, input_width, 3), dtype=np.uint8)
        self._latencies_milliseconds: deque[float] = deque(maxlen=LATENCY_WINDOW_SIZE)

    @property
    def latency_percentile_milliseconds(self) -> float | None:
        """The 95th percentile of recent round trips to the detector, or None before the first."""
        if not self._latencies_milliseconds:
            return None
        return float(np.percentile(self._latencies_milliseconds, LATENCY_PERCENTILE))

    async def detect(self, frame: Frame) -> list[Detection] | None:
        """Returns the objects found in the frame, or None when the detector does not answer.

        Raises:
            ValueError: The frame is larger than the model's input.
        """
        frame_height, frame_width = frame.image.shape[:2]
        if frame_width > self._input_width or frame_height > self._input_height:
            raise ValueError(f"frame of {frame_width}x{frame_height} does not fit the model input")
        padding_x = (self._input_width - frame_width) // 2
        padding_y = (self._input_height - frame_height) // 2
        self._letterboxed_image.fill(LETTERBOX_PADDING_INTENSITY)
        self._letterboxed_image[
            padding_y : padding_y + frame_height, padding_x : padding_x + frame_width
        ] = frame.image
        self._frame_writer.write(self._letterboxed_image, frame.sequence_number)

        request = DetectionRequest(
            camera_id=self._camera_id,
            shared_memory_name=self._frame_writer.name,
            frame_sequence_number=frame.sequence_number,
            input_width=self._input_width,
            input_height=self._input_height,
            scale=1.0,
            padding_x_pixels=padding_x,
            padding_y_pixels=padding_y,
            frame_width_pixels=frame_width,
            frame_height_pixels=frame_height,
        )
        started_at = time.monotonic()
        try:
            raw_reply = await self._message_bus.request(
                DETECTION_REQUESTS_SUBJECT,
                request.model_dump_json().encode(),
                DETECTION_TIMEOUT_SECONDS,
            )
        except NatsError as error:
            logger.warning(
                "detector did not answer: %s", error, extra={"camera_id": self._camera_id}
            )
            return None
        reply = DetectionReply.model_validate_json(raw_reply)
        self._latencies_milliseconds.append(
            (time.monotonic() - started_at) * MILLISECONDS_PER_SECOND
        )
        return [
            Detection(
                object_class=detected_object.object_class,
                confidence_ratio=detected_object.confidence_ratio,
                bounding_box=BoundingBox(
                    x=detected_object.x,
                    y=detected_object.y,
                    width=detected_object.width,
                    height=detected_object.height,
                ),
            )
            for detected_object in reply.detected_objects
        ]

    def close(self) -> None:
        """Removes the camera's shared memory region."""
        self._frame_writer.close()
