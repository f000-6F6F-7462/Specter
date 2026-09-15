"""Decides which frames of a camera are worth running models on."""

from enum import Enum, auto

import numpy as np
from numpy.typing import NDArray

from specter.entities.cameras import SamplingMode, SamplingSettings
from specter.vision.frames import Frame, FrameImage

MOTION_THUMBNAIL_SIZE_PIXELS = 32
DEFAULT_MOTION_THRESHOLD_INTENSITY = 2.0
DEFAULT_RATE_DECREASE_FACTOR = 0.5
DEFAULT_RATE_INCREASE_FPS = 0.5
MILLISECONDS_PER_SECOND = 1000.0


class SamplingDecision(Enum):
    """What to do with a frame."""

    PROCESS = auto()
    SKIP_ABOVE_RATE = auto()
    SKIP_WITHOUT_MOTION = auto()


class FrameSampler:
    """Admits frames up to the camera's current rate and skips frames without motion.

    In adaptive mode the rate backs off multiplicatively toward the minimum while detection cannot
    keep up, and recovers step by step toward the target once it can; recovering gradually avoids
    oscillating around the limit. Fixed mode always keeps the target rate.
    """

    def __init__(
        self,
        settings: SamplingSettings,
        *,
        motion_threshold_intensity: float = DEFAULT_MOTION_THRESHOLD_INTENSITY,
        rate_decrease_factor: float = DEFAULT_RATE_DECREASE_FACTOR,
        rate_increase_fps: float = DEFAULT_RATE_INCREASE_FPS,
    ) -> None:
        self._settings = settings
        self._motion_threshold_intensity = motion_threshold_intensity
        self._rate_decrease_factor = rate_decrease_factor
        self._rate_increase_fps = rate_increase_fps
        self._effective_fps = settings.target_fps
        self._next_admitted_presentation_time_seconds: float | None = None
        self._previous_motion_thumbnail: NDArray[np.float64] | None = None

    @property
    def effective_fps(self) -> float:
        """The rate frames are currently admitted at."""
        return self._effective_fps

    def decide(self, frame: Frame) -> SamplingDecision:
        """Returns whether to process the frame, remembering it when it is admitted."""
        if (
            self._next_admitted_presentation_time_seconds is not None
            and frame.presentation_time_seconds < self._next_admitted_presentation_time_seconds
        ):
            return SamplingDecision.SKIP_ABOVE_RATE

        if self._settings.is_motion_gating_enabled:
            motion_thumbnail = build_motion_thumbnail(frame.image)
            if self._previous_motion_thumbnail is not None:
                mean_change_intensity = float(
                    np.abs(motion_thumbnail - self._previous_motion_thumbnail).mean()
                )
                if mean_change_intensity < self._motion_threshold_intensity:
                    return SamplingDecision.SKIP_WITHOUT_MOTION
            self._previous_motion_thumbnail = motion_thumbnail

        # Scheduling from the admitted frame's timestamp keeps an exact-rate source from losing
        # frames to floating-point error, and stops a late burst from being admitted all at once.
        self._next_admitted_presentation_time_seconds = (
            frame.presentation_time_seconds + 1.0 / self._effective_fps
        )
        return SamplingDecision.PROCESS

    def adapt_to_detection_latency(self, detection_latency_p95_milliseconds: float) -> None:
        """Adjusts the admitted rate to how fast detection currently runs.

        Fixed mode ignores the latency.
        """
        if self._settings.mode is SamplingMode.FIXED:
            return
        frame_budget_milliseconds = MILLISECONDS_PER_SECOND / self._effective_fps
        if detection_latency_p95_milliseconds > frame_budget_milliseconds:
            self._effective_fps = max(
                self._effective_fps * self._rate_decrease_factor, self._settings.minimum_fps
            )
        else:
            self._effective_fps = min(
                self._effective_fps + self._rate_increase_fps, self._settings.target_fps
            )


def build_motion_thumbnail(image: FrameImage) -> NDArray[np.float64]:
    """Returns a small grayscale copy of the image for cheap motion comparison."""
    step = max(
        image.shape[0] // MOTION_THUMBNAIL_SIZE_PIXELS,
        image.shape[1] // MOTION_THUMBNAIL_SIZE_PIXELS,
        1,
    )
    return np.asarray(image[::step, ::step].mean(axis=2), dtype=np.float64)
