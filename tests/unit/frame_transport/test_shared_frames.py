import time

import numpy as np

from specter.frame_transport.shared_frames import SharedFrameReader, SharedFrameWriter

WIDTH_PIXELS = 64
HEIGHT_PIXELS = 48
# macOS rejects shared memory names longer than this, counting the leading slash.
MAXIMUM_MACOS_NAME_LENGTH = 31


def build_camera_id() -> str:
    return f"camera_{time.time_ns():032x}"


def test_detector_reads_the_written_frame_when_sequence_numbers_match() -> None:
    frame_writer = SharedFrameWriter(build_camera_id(), WIDTH_PIXELS, HEIGHT_PIXELS)
    image = np.random.default_rng(1).integers(0, 256, (HEIGHT_PIXELS, WIDTH_PIXELS, 3), np.uint8)
    try:
        frame_writer.write(image, sequence_number=7)
        frame_reader = SharedFrameReader(frame_writer.name, WIDTH_PIXELS, HEIGHT_PIXELS)
        try:
            read_image = frame_reader.read(sequence_number=7)
        finally:
            frame_reader.close()
    finally:
        frame_writer.close()

    assert read_image is not None
    np.testing.assert_array_equal(read_image, image)


def test_reading_returns_nothing_when_region_holds_another_frame() -> None:
    frame_writer = SharedFrameWriter(build_camera_id(), WIDTH_PIXELS, HEIGHT_PIXELS)
    try:
        frame_writer.write(np.zeros((HEIGHT_PIXELS, WIDTH_PIXELS, 3), np.uint8), sequence_number=8)
        frame_reader = SharedFrameReader(frame_writer.name, WIDTH_PIXELS, HEIGHT_PIXELS)
        try:
            read_image = frame_reader.read(sequence_number=7)
        finally:
            frame_reader.close()
    finally:
        frame_writer.close()

    assert read_image is None


def test_region_name_fits_macos_limit_when_camera_id_is_long() -> None:
    frame_writer = SharedFrameWriter(build_camera_id(), WIDTH_PIXELS, HEIGHT_PIXELS)
    try:
        name_length = len(f"/{frame_writer.name}")
    finally:
        frame_writer.close()

    assert name_length <= MAXIMUM_MACOS_NAME_LENGTH


def test_stale_region_is_replaced_when_camera_starts_again() -> None:
    camera_id = build_camera_id()
    first_writer = SharedFrameWriter(camera_id, WIDTH_PIXELS, HEIGHT_PIXELS)

    second_writer = SharedFrameWriter(camera_id, WIDTH_PIXELS, HEIGHT_PIXELS)
    frame_reader = SharedFrameReader(second_writer.name, WIDTH_PIXELS, HEIGHT_PIXELS)
    second_writer.write(np.ones((HEIGHT_PIXELS, WIDTH_PIXELS, 3), np.uint8), sequence_number=3)
    try:
        read_image = frame_reader.read(sequence_number=3)
    finally:
        frame_reader.close()
        first_writer.close()
        second_writer.close()

    assert read_image is not None
