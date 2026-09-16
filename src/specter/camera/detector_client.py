"""A camera process's link to the detector: its shared frame region and its requests."""

import logging
import time
from collections import deque
from collections.abc import Sequence

import numpy as np
from nats.errors import Error as NatsError

from specter.entities.geometry import BoundingBox
from specter.frame_transport.detector_requests import (
    DETECTION_REQUESTS_SUBJECT,
    IDENTIFICATION_REQUESTS_SUBJECT,
    DetectionReply,
    DetectionRequest,
    IdentificationReply,
    IdentificationRequest,
    IdentificationResult,
    IdentificationTask,
    SharedFrameReference,
)
from specter.frame_transport.shared_frames import SharedFrameWriter
from specter.messaging.client import MessageBus
from specter.vision.detections import Detection
from specter.vision.frames import Frame

logger = logging.getLogger(__name__)

DETECTION_TIMEOUT_SECONDS = 2.0
# Identification runs a face model per person and waits its turn behind other cameras' people.
IDENTIFICATION_TIMEOUT_SECONDS = 5.0
LATENCY_WINDOW_SIZE = 20
LATENCY_PERCENTILE = 95
MILLISECONDS_PER_SECOND = 1000.0


class DetectorClient:
    """Sends a camera's frames to the detector one at a time and returns what it found.

    Each frame is written whole to the camera's shared memory region, which is created again when
    the stream's frame size changes. Identification reads the frame that detection wrote, so it
    must follow the detection of the same frame.
    """

    def __init__(self, camera_id: str, message_bus: MessageBus) -> None:
        self._camera_id = camera_id
        self._message_bus = message_bus
        self._frame_writer: SharedFrameWriter | None = None
        self._latencies_milliseconds: deque[float] = deque(maxlen=LATENCY_WINDOW_SIZE)
        self._is_detector_answering = True

    @property
    def latency_percentile_milliseconds(self) -> float | None:
        """The 95th percentile of recent detection round trips, or None before the first."""
        if not self._latencies_milliseconds:
            return None
        return float(np.percentile(self._latencies_milliseconds, LATENCY_PERCENTILE))

    async def detect(self, frame: Frame) -> list[Detection] | None:
        """Returns the objects found in the frame, or None when the detector does not answer."""
        frame_writer = self._prepare_frame_writer(frame)
        frame_writer.write(frame.image, frame.sequence_number)
        request = DetectionRequest(frame=self._build_frame_reference(frame, frame_writer))
        started_at = time.monotonic()
        try:
            raw_reply = await self._message_bus.request(
                DETECTION_REQUESTS_SUBJECT,
                request.model_dump_json().encode(),
                DETECTION_TIMEOUT_SECONDS,
            )
        except NatsError as error:
            self._record_detector_silence(error)
            return None
        self._record_detector_answer()
        reply = DetectionReply.model_validate_json(raw_reply)
        self._latencies_milliseconds.append(
            (time.monotonic() - started_at) * MILLISECONDS_PER_SECOND
        )
        return [
            Detection(
                object_class=detected_object.object_class,
                confidence_ratio=detected_object.confidence_ratio,
                bounding_box=BoundingBox(
                    x=detected_object.box.x,
                    y=detected_object.box.y,
                    width=detected_object.box.width,
                    height=detected_object.box.height,
                ),
            )
            for detected_object in reply.detected_objects
        ]

    async def identify(
        self, frame: Frame, tasks: Sequence[IdentificationTask]
    ) -> list[IdentificationResult] | None:
        """Returns the embeddings of tracked people in the frame that ``detect`` last sent.

        Returns None when the detector does not answer, and an empty list when the frame was
        replaced before the detector read it.
        """
        if self._frame_writer is None:
            raise RuntimeError("identification needs the frame to be sent to detection first")
        request = IdentificationRequest(
            frame=self._build_frame_reference(frame, self._frame_writer), tasks=tuple(tasks)
        )
        try:
            raw_reply = await self._message_bus.request(
                IDENTIFICATION_REQUESTS_SUBJECT,
                request.model_dump_json().encode(),
                IDENTIFICATION_TIMEOUT_SECONDS,
            )
        except NatsError as error:
            self._record_detector_silence(error)
            return None
        self._record_detector_answer()
        return list(IdentificationReply.model_validate_json(raw_reply).results)

    def close(self) -> None:
        """Removes the camera's shared memory region."""
        if self._frame_writer is not None:
            self._frame_writer.close()
            self._frame_writer = None

    def _record_detector_silence(self, error: NatsError) -> None:
        # Frames keep arriving while the detector is down, so only the change is logged, rather
        # than a warning for every frame filling the device's disk.
        if self._is_detector_answering:
            logger.warning(
                "detector stopped answering: %s", error, extra={"camera_id": self._camera_id}
            )
        self._is_detector_answering = False

    def _record_detector_answer(self) -> None:
        if not self._is_detector_answering:
            logger.info("detector answers again", extra={"camera_id": self._camera_id})
        self._is_detector_answering = True

    def _build_frame_reference(
        self, frame: Frame, frame_writer: SharedFrameWriter
    ) -> SharedFrameReference:
        return SharedFrameReference(
            camera_id=self._camera_id,
            shared_memory_name=frame_writer.name,
            frame_sequence_number=frame.sequence_number,
            frame_width_pixels=frame.width_pixels,
            frame_height_pixels=frame.height_pixels,
        )

    def _prepare_frame_writer(self, frame: Frame) -> SharedFrameWriter:
        if self._frame_writer is not None and self._frame_writer.matches_size(
            frame.width_pixels, frame.height_pixels
        ):
            return self._frame_writer
        self.close()
        self._frame_writer = SharedFrameWriter(
            self._camera_id, frame.width_pixels, frame.height_pixels
        )
        return self._frame_writer
