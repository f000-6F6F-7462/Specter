"""Reads a camera's video stream and decodes it into frames."""

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime

import av
import numpy as np

from specter.entities.cameras import CameraStatus
from specter.vision.frames import Frame, FrameImage

logger = logging.getLogger(__name__)

# FFmpeg takes stream options as text; the timeout is in microseconds.
STREAM_OPTIONS = {"rtsp_transport": "tcp", "timeout": "5000000"}
DECODED_PIXEL_FORMAT = "bgr24"
INITIAL_RECONNECT_DELAY_SECONDS = 1.0
MAXIMUM_RECONNECT_DELAY_SECONDS = 30.0
READER_STOP_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class _DecodedFrame:
    video_frame: av.VideoFrame
    sequence_number: int
    presentation_time_seconds: float
    captured_at: datetime


class StreamReader:
    """Decodes a video stream on a background thread and hands over only its newest frame.

    Frames larger than the given size are scaled down to fit it while they are converted. Analysis
    runs slower than the camera, so a frame that arrives while analysis is busy replaces the
    waiting one instead of queuing behind it. A stream that fails or cannot be opened is opened
    again with growing delays until the reader is stopped. Must be created inside the running
    event loop.
    """

    def __init__(
        self,
        camera_id: str,
        stream_url: str,
        *,
        maximum_width_pixels: int,
        maximum_height_pixels: int,
    ) -> None:
        self._camera_id = camera_id
        self._stream_url = stream_url
        self._maximum_width_pixels = maximum_width_pixels
        self._maximum_height_pixels = maximum_height_pixels
        self._status = CameraStatus.STARTING
        self._decoded_frame_count = 0
        self._returned_sequence_number = 0
        self._last_presentation_time_seconds = 0.0
        self._latest_decoded_frame: _DecodedFrame | None = None
        self._latest_frame_lock = threading.Lock()
        self._stop_requested = threading.Event()
        self._event_loop = asyncio.get_running_loop()
        self._frame_available = asyncio.Event()
        self._thread = threading.Thread(
            target=self._read_until_stopped, name=f"stream-reader-{camera_id}", daemon=True
        )

    @property
    def status(self) -> CameraStatus:
        """Whether frames arrive: starting, then running, or reconnecting after a failure."""
        return self._status

    def start(self) -> None:
        """Starts reading on the background thread."""
        self._thread.start()

    async def stop(self) -> None:
        """Stops reading and waits for the background thread to finish."""
        self._stop_requested.set()
        await asyncio.to_thread(self._thread.join, READER_STOP_TIMEOUT_SECONDS)

    async def next_frame(self) -> Frame:
        """Waits for a frame newer than the one returned last, and returns it."""
        decoded_frame = self._take_newer_frame()
        while decoded_frame is None:
            await self._frame_available.wait()
            self._frame_available.clear()
            decoded_frame = self._take_newer_frame()
        # Converting only the frames that analysis takes spares the work for every replaced frame.
        image = await asyncio.to_thread(self._convert_to_image, decoded_frame.video_frame)
        return Frame(
            camera_id=self._camera_id,
            sequence_number=decoded_frame.sequence_number,
            presentation_time_seconds=decoded_frame.presentation_time_seconds,
            captured_at=decoded_frame.captured_at,
            image=image,
        )

    def _convert_to_image(self, video_frame: av.VideoFrame) -> FrameImage:
        scale = min(
            self._maximum_width_pixels / video_frame.width,
            self._maximum_height_pixels / video_frame.height,
            1.0,
        )
        # Scaling while converting from the decoder's format never builds a full-size BGR image.
        scaled_frame = video_frame.reformat(
            width=max(round(video_frame.width * scale), 1),
            height=max(round(video_frame.height * scale), 1),
            format=DECODED_PIXEL_FORMAT,
        )
        # BGR always has 8-bit pixels, so no copy is made here.
        return np.asarray(scaled_frame.to_ndarray(), dtype=np.uint8)

    def _take_newer_frame(self) -> _DecodedFrame | None:
        with self._latest_frame_lock:
            decoded_frame = self._latest_decoded_frame
        if decoded_frame is None or decoded_frame.sequence_number <= self._returned_sequence_number:
            return None
        self._returned_sequence_number = decoded_frame.sequence_number
        return decoded_frame

    def _read_until_stopped(self) -> None:
        reconnect_delay_seconds = INITIAL_RECONNECT_DELAY_SECONDS
        while not self._stop_requested.is_set():
            frame_count_before_attempt = self._decoded_frame_count
            try:
                self._read_stream()
            except (av.FFmpegError, OSError) as error:
                logger.warning(
                    "camera stream failed: %s", error, extra={"camera_id": self._camera_id}
                )
            except Exception:
                logger.exception("camera stream failed", extra={"camera_id": self._camera_id})
            if self._stop_requested.is_set():
                return
            self._status = CameraStatus.RECONNECTING
            if self._decoded_frame_count > frame_count_before_attempt:
                reconnect_delay_seconds = INITIAL_RECONNECT_DELAY_SECONDS
            self._stop_requested.wait(reconnect_delay_seconds)
            reconnect_delay_seconds = min(
                reconnect_delay_seconds * 2, MAXIMUM_RECONNECT_DELAY_SECONDS
            )

    def _read_stream(self) -> None:
        with av.open(self._stream_url, options=STREAM_OPTIONS) as container:
            video_stream = container.streams.video[0]
            # Decoding on several threads keeps high resolutions within real time.
            video_stream.thread_type = "AUTO"
            for video_frame in container.decode(video_stream):
                if self._stop_requested.is_set():
                    return
                self._hand_over(video_frame)

    def _hand_over(self, video_frame: av.VideoFrame) -> None:
        self._decoded_frame_count += 1
        # A frame without a timestamp, such as the first frame of a restream, keeps the previous
        # frame's, so every timestamp stays on the stream's own timeline.
        if video_frame.time is not None:
            self._last_presentation_time_seconds = float(video_frame.time)
        decoded_frame = _DecodedFrame(
            video_frame=video_frame,
            sequence_number=self._decoded_frame_count,
            presentation_time_seconds=self._last_presentation_time_seconds,
            captured_at=datetime.now(UTC),
        )
        with self._latest_frame_lock:
            self._latest_decoded_frame = decoded_frame
        self._status = CameraStatus.RUNNING
        self._event_loop.call_soon_threadsafe(self._frame_available.set)
