import asyncio
import time

import numpy as np

from specter.detector.batcher import DetectionBatcher
from specter.frame_transport.detector_requests import DetectionRequest, SharedFrameReference
from specter.frame_transport.shared_frames import SharedFrameWriter
from specter.inference.backends import Tensor
from specter.inference.object_detector import COCO_CLASS_NAMES, ObjectDetector

INPUT_WIDTH_PIXELS = 64
INPUT_HEIGHT_PIXELS = 64
FRAME_WIDTH_PIXELS = 160
FRAME_HEIGHT_PIXELS = 90
CANDIDATE_COUNT = 21
BATCH_DELAY_SECONDS = 0.2


class RecordingSession:
    def __init__(self) -> None:
        self.batch_sizes: list[int] = []
        self.input_shapes: list[tuple[int, ...]] = []

    def run(self, inputs: Tensor) -> list[list[Tensor]]:
        self.batch_sizes.append(len(inputs))
        self.input_shapes.append(inputs.shape)
        empty_output = np.zeros((1, 4 + len(COCO_CLASS_NAMES), CANDIDATE_COUNT), np.float32)
        return [[empty_output] for _ in inputs]


def build_request(frame_writer: SharedFrameWriter, sequence_number: int) -> DetectionRequest:
    return DetectionRequest(
        frame=SharedFrameReference(
            camera_id="camera_front_door",
            shared_memory_name=frame_writer.name,
            frame_sequence_number=sequence_number,
            frame_width_pixels=FRAME_WIDTH_PIXELS,
            frame_height_pixels=FRAME_HEIGHT_PIXELS,
        )
    )


async def run_batcher(
    session: RecordingSession, max_batch_size: int, sequence_numbers: list[int]
) -> list[int]:
    frame_writers = [
        SharedFrameWriter(
            f"camera_{time.time_ns()}_{index}", FRAME_WIDTH_PIXELS, FRAME_HEIGHT_PIXELS
        )
        for index in range(2)
    ]
    for frame_writer in frame_writers:
        frame_writer.write(np.zeros((FRAME_HEIGHT_PIXELS, FRAME_WIDTH_PIXELS, 3), np.uint8), 1)
    batcher = DetectionBatcher(
        session,
        ObjectDetector(INPUT_WIDTH_PIXELS, INPUT_HEIGHT_PIXELS),
        max_batch_size=max_batch_size,
        max_batch_delay_seconds=BATCH_DELAY_SECONDS,
    )
    batching_task = asyncio.create_task(batcher.run())
    try:
        replies = await asyncio.gather(
            *(
                batcher.detect(build_request(frame_writer, sequence_number))
                for frame_writer, sequence_number in zip(
                    frame_writers, sequence_numbers, strict=True
                )
            )
        )
    finally:
        batching_task.cancel()
        await asyncio.gather(batching_task, return_exceptions=True)
        batcher.close()
        for frame_writer in frame_writers:
            frame_writer.close()
    return [len(reply.detected_objects) for reply in replies]


async def test_requests_arriving_together_run_as_one_batch_when_batch_has_room() -> None:
    session = RecordingSession()

    object_counts = await run_batcher(session, max_batch_size=4, sequence_numbers=[1, 1])

    assert session.batch_sizes == [2]
    assert object_counts == [0, 0]


async def test_frames_are_letterboxed_to_model_input_when_they_are_larger() -> None:
    session = RecordingSession()

    await run_batcher(session, max_batch_size=4, sequence_numbers=[1, 1])

    assert session.input_shapes == [(2, 3, INPUT_HEIGHT_PIXELS, INPUT_WIDTH_PIXELS)]


async def test_requests_run_separately_when_batch_size_is_one() -> None:
    session = RecordingSession()

    await run_batcher(session, max_batch_size=1, sequence_numbers=[1, 1])

    assert session.batch_sizes == [1, 1]


async def test_replaced_frame_gets_an_empty_reply_without_running_the_model() -> None:
    session = RecordingSession()

    object_counts = await run_batcher(session, max_batch_size=4, sequence_numbers=[2, 1])

    assert session.batch_sizes == [1]
    assert object_counts == [0, 0]
