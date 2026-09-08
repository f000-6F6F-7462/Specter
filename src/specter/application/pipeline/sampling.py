"""Frame admission: the first place frames get dropped.

Two independent gates, cheapest first:

* **rate cap** — never admit faster than ``target_fps`` (measured on *source*
  timestamps, so it is unaffected by how fast we read the connection);
* **motion gate** — when enabled, skip frames whose downscaled greyscale barely changed
  since the last admitted frame.

Overload shedding (dropping when inference falls behind) happens after this, at the
bounded hand-off queue in the runner.
"""

from dataclasses import dataclass
from enum import Enum, auto

import numpy as np

from specter.domain.streams import SamplingConfig
from specter.domain.vision import Frame

_DOWNSCALE_TO = 32


class SampleOutcome(Enum):
    PROCESS = auto()
    DROP_RATE = auto()
    DROP_MOTION = auto()


@dataclass(slots=True)
class SamplerStats:
    admitted: int = 0
    dropped_rate: int = 0
    dropped_motion: int = 0


class AdaptiveSampler:
    def __init__(self, sampling: SamplingConfig, *, motion_min_delta: float = 2.0) -> None:
        self._min_gap = 1.0 / sampling.target_fps
        self._motion_gating = sampling.motion_gating
        self._motion_min_delta = motion_min_delta
        self._next_ts: float | None = None
        self._ref: np.ndarray | None = None
        self.stats = SamplerStats()

    def classify(self, frame: Frame) -> SampleOutcome:
        # Running deadline (add the gap, never subtract timestamps) so exact-fps sources
        # don't lose a frame to float error, and a late burst can't catch up unbounded.
        if self._next_ts is not None and frame.ts < self._next_ts:
            self.stats.dropped_rate += 1
            return SampleOutcome.DROP_RATE

        thumb = self._thumbnail(frame.image)
        if self._motion_gating and self._ref is not None:
            delta = float(np.abs(thumb - self._ref).mean())
            if delta < self._motion_min_delta:
                self.stats.dropped_motion += 1
                return SampleOutcome.DROP_MOTION

        self._next_ts = frame.ts + self._min_gap
        self._ref = thumb
        self.stats.admitted += 1
        return SampleOutcome.PROCESS

    @staticmethod
    def _thumbnail(image: np.ndarray) -> np.ndarray:
        step = max(image.shape[0] // _DOWNSCALE_TO, image.shape[1] // _DOWNSCALE_TO, 1)
        return image[::step, ::step].mean(axis=2)
