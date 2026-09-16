"""Collects object detection requests from every camera into batches, one model call per batch."""

import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass

from specter.frame_transport.detection_requests import (
    DetectedObject,
    DetectionReply,
    DetectionRequest,
)
from specter.frame_transport.shared_frames import SharedFrameReader
from specter.inference.backends import InferenceSession
from specter.inference.object_detector import LetterboxTransform, ObjectDetector
from specter.vision.frames import FrameImage

logger = logging.getLogger(__name__)

EMPTY_REPLY = DetectionReply(detected_objects=())


@dataclass(frozen=True, slots=True)
class _PendingRequest:
    request: DetectionRequest
    reply: asyncio.Future[DetectionReply]


class DetectionBatcher:
    """Runs object detection for all cameras, batching the requests that arrive close together.

    A batch opens with the first waiting request and closes when it is full or the batch delay has
    passed, so a lone camera waits at most the delay. The model runs on a worker thread, which
    leaves the event loop free to take more requests meanwhile.
    """

    def __init__(
        self,
        session: InferenceSession,
        object_detector: ObjectDetector,
        *,
        max_batch_size: int,
        max_batch_delay_seconds: float,
    ) -> None:
        self._session = session
        self._object_detector = object_detector
        self._max_batch_size = max_batch_size
        self._max_batch_delay_seconds = max_batch_delay_seconds
        self._pending_requests: asyncio.Queue[_PendingRequest] = asyncio.Queue()
        self._frame_readers: dict[str, SharedFrameReader] = {}

    async def answer(self, raw_request: bytes) -> bytes:
        """Answers a serialized detection request with the serialized reply."""
        reply = await self.detect(DetectionRequest.model_validate_json(raw_request))
        return reply.model_dump_json().encode()

    async def detect(self, request: DetectionRequest) -> DetectionReply:
        """Waits for the request's batch to run and returns the objects found in its frame."""
        reply: asyncio.Future[DetectionReply] = asyncio.get_running_loop().create_future()
        await self._pending_requests.put(_PendingRequest(request=request, reply=reply))
        return await reply

    async def run(self) -> None:
        """Forms and runs batches until the task is cancelled."""
        while True:
            batch = await self._collect_batch()
            try:
                replies = await asyncio.to_thread(
                    self._detect_batch, [pending.request for pending in batch]
                )
            except Exception as error:
                logger.exception("object detection failed", extra={"batch_size": len(batch)})
                for pending in batch:
                    if not pending.reply.done():
                        pending.reply.set_exception(error)
                continue
            for pending, reply in zip(batch, replies, strict=True):
                if not pending.reply.done():
                    pending.reply.set_result(reply)

    def close(self) -> None:
        """Detaches from every camera's shared memory region."""
        for frame_reader in self._frame_readers.values():
            frame_reader.close()
        self._frame_readers.clear()

    async def _collect_batch(self) -> list[_PendingRequest]:
        batch = [await self._pending_requests.get()]
        deadline = time.monotonic() + self._max_batch_delay_seconds
        while len(batch) < self._max_batch_size:
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                break
            try:
                batch.append(
                    await asyncio.wait_for(self._pending_requests.get(), remaining_seconds)
                )
            except TimeoutError:
                break
        return batch

    def _detect_batch(self, requests: Sequence[DetectionRequest]) -> list[DetectionReply]:
        replies = [EMPTY_REPLY] * len(requests)
        readable_frames = [
            (index, image)
            for index, image in enumerate(self._read_frame(request) for request in requests)
            if image is not None
        ]
        if not readable_frames:
            return replies
        all_outputs = self._session.run(
            self._object_detector.build_input_tensor([image for _, image in readable_frames])
        )
        for (index, _), outputs in zip(readable_frames, all_outputs, strict=True):
            request = requests[index]
            transform = LetterboxTransform(
                scale=request.scale,
                padding_x_pixels=request.padding_x_pixels,
                padding_y_pixels=request.padding_y_pixels,
                frame_width_pixels=request.frame_width_pixels,
                frame_height_pixels=request.frame_height_pixels,
            )
            replies[index] = DetectionReply(
                detected_objects=tuple(
                    DetectedObject(
                        object_class=detection.object_class,
                        confidence_ratio=detection.confidence_ratio,
                        x=detection.bounding_box.x,
                        y=detection.bounding_box.y,
                        width=detection.bounding_box.width,
                        height=detection.bounding_box.height,
                    )
                    for detection in self._object_detector.decode(outputs, transform)
                )
            )
        return replies

    def _read_frame(self, request: DetectionRequest) -> FrameImage | None:
        frame_reader = self._frame_readers.get(request.shared_memory_name)
        if frame_reader is not None and frame_reader.matches_size(
            request.input_width, request.input_height
        ):
            image = frame_reader.read(request.frame_sequence_number)
            if image is not None:
                return image
        # A restarted camera creates a new region under the same name, which needs a new attachment.
        if frame_reader is not None:
            frame_reader.close()
        try:
            frame_reader = SharedFrameReader(
                request.shared_memory_name, request.input_width, request.input_height
            )
        except FileNotFoundError:
            self._frame_readers.pop(request.shared_memory_name, None)
            return None
        self._frame_readers[request.shared_memory_name] = frame_reader
        return frame_reader.read(request.frame_sequence_number)
