"""Frames that a camera process shares with the detector process through shared memory.

Each camera owns one region that holds the frame it is analyzing, at the size the camera decodes it.
The camera writes a frame only after the detector answered the previous one, so a single region
needs no locking.
"""

import hashlib
from contextlib import suppress
from multiprocessing import resource_tracker, shared_memory

import numpy as np

from specter.vision.frames import FrameImage

SHARED_MEMORY_NAME_PREFIX = "specter_"
# macOS limits a shared memory name to 31 characters, which a camera id alone exceeds.
NAME_HASH_CHARACTER_COUNT = 16
COLOR_CHANNEL_COUNT = 3
SEQUENCE_NUMBER_DTYPE = np.dtype("<u8")
HEADER_SIZE_BYTES = SEQUENCE_NUMBER_DTYPE.itemsize


class SharedFrameWriter:
    """A camera's shared memory region, which it creates, writes and removes.

    The creating process's resource tracker removes the region if the process dies without
    closing it, so a crashed camera leaves nothing behind.
    """

    def __init__(self, camera_id: str, width_pixels: int, height_pixels: int) -> None:
        camera_hash = hashlib.sha256(camera_id.encode()).hexdigest()[:NAME_HASH_CHARACTER_COUNT]
        self._name = f"{SHARED_MEMORY_NAME_PREFIX}{camera_hash}"
        self._width_pixels = width_pixels
        self._height_pixels = height_pixels
        self._remove_stale_region()
        self._region = shared_memory.SharedMemory(
            name=self._name,
            create=True,
            size=HEADER_SIZE_BYTES + width_pixels * height_pixels * COLOR_CHANNEL_COUNT,
        )
        self._sequence_number = np.ndarray(
            (1,), dtype=SEQUENCE_NUMBER_DTYPE, buffer=self._region.buf
        )
        self._image = np.ndarray(
            (height_pixels, width_pixels, COLOR_CHANNEL_COUNT),
            dtype=np.uint8,
            buffer=self._region.buf,
            offset=HEADER_SIZE_BYTES,
        )

    @property
    def name(self) -> str:
        """The region's name, which the detector attaches by."""
        return self._name

    def matches_size(self, width_pixels: int, height_pixels: int) -> bool:
        """Whether the region holds images of the given size."""
        return (width_pixels, height_pixels) == (self._width_pixels, self._height_pixels)

    def write(self, image: FrameImage, sequence_number: int) -> None:
        """Copies an image of the region's size in, stamped with its frame's sequence number."""
        self._image[:] = image
        self._sequence_number[0] = sequence_number

    def close(self) -> None:
        """Removes the region, unless a restarted camera already replaced it."""
        # The views must go before the buffer they point into can be released.
        del self._sequence_number, self._image
        self._region.close()
        with suppress(FileNotFoundError):
            self._region.unlink()

    def _remove_stale_region(self) -> None:
        # A region left by a camera process that was killed before its tracker ran is replaced.
        try:
            stale_region = shared_memory.SharedMemory(name=self._name)
        except FileNotFoundError:
            return
        stale_region.close()
        stale_region.unlink()


class SharedFrameReader:
    """The detector's view of a camera's shared memory region, which the camera owns."""

    def __init__(self, name: str, width_pixels: int, height_pixels: int) -> None:
        """Attaches to the camera's region.

        Raises:
            FileNotFoundError: No region of that name holds images of the given size, because the
                camera stopped or is replacing its region for a new frame size.
        """
        self._region = shared_memory.SharedMemory(name=name)
        # Attaching registers the region with this process's resource tracker, which would remove
        # it when the detector exits although the camera still uses it.
        resource_tracker.unregister(f"/{self._region.name}", "shared_memory")
        if (
            self._region.size
            < HEADER_SIZE_BYTES + width_pixels * height_pixels * COLOR_CHANNEL_COUNT
        ):
            self._region.close()
            raise FileNotFoundError(f"shared memory region {name} is smaller than the frame")
        self._width_pixels = width_pixels
        self._height_pixels = height_pixels
        self._sequence_number = np.ndarray(
            (1,), dtype=SEQUENCE_NUMBER_DTYPE, buffer=self._region.buf
        )
        self._image = np.ndarray(
            (height_pixels, width_pixels, COLOR_CHANNEL_COUNT),
            dtype=np.uint8,
            buffer=self._region.buf,
            offset=HEADER_SIZE_BYTES,
        )

    def matches_size(self, width_pixels: int, height_pixels: int) -> bool:
        """Whether the region holds images of the given size."""
        return (width_pixels, height_pixels) == (self._width_pixels, self._height_pixels)

    def read(self, sequence_number: int) -> FrameImage | None:
        """Returns a copy of the frame, or None when the region holds a different frame."""
        if int(self._sequence_number[0]) != sequence_number:
            return None
        return self._image.copy()

    def close(self) -> None:
        """Detaches from the region without removing it."""
        del self._sequence_number, self._image
        self._region.close()


class SharedFrameReaders:
    """The detector's attachments to every camera's region, kept between requests.

    Not thread-safe: each detector component that reads frames on its own thread keeps its own.
    """

    def __init__(self) -> None:
        self._frame_readers: dict[str, SharedFrameReader] = {}

    def read(
        self, name: str, width_pixels: int, height_pixels: int, sequence_number: int
    ) -> FrameImage | None:
        """Returns a copy of the frame, or None when the camera already replaced or removed it."""
        frame_reader = self._frame_readers.get(name)
        if frame_reader is not None and frame_reader.matches_size(width_pixels, height_pixels):
            image = frame_reader.read(sequence_number)
            if image is not None:
                return image
        # A restarted camera, or one whose frame size changed, creates a new region under the same
        # name, which needs a new attachment.
        if frame_reader is not None:
            frame_reader.close()
            del self._frame_readers[name]
        try:
            frame_reader = SharedFrameReader(name, width_pixels, height_pixels)
        except FileNotFoundError:
            return None
        self._frame_readers[name] = frame_reader
        return frame_reader.read(sequence_number)

    def close(self) -> None:
        """Detaches from every region."""
        for frame_reader in self._frame_readers.values():
            frame_reader.close()
        self._frame_readers.clear()
