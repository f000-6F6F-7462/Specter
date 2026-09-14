"""Frame admission: the first place frames get dropped.

Two independent gates, cheapest first:

* **rate cap** — never admit faster than the current *effective* fps (measured on
  *source* timestamps, so it is unaffected by how fast we read the connection);
* **motion gate** — when enabled, skip frames whose downscaled greyscale barely changed
  since the last admitted frame.

The effective fps starts at ``target_fps`` and moves under AIMD control
(``adjust_for_latency``): multiplicatively back off toward ``min_fps`` when inference
can't keep up with the current rate, additively climb back toward ``target_fps`` once it
can — the classic shape, chosen so a slow model degrades the stream's own frame rate
rather than the queue silently shedding a growing fraction of it.

Overload shedding (dropping when inference falls behind despite that) happens after
this, at the bounded hand-off queue in the runner.
"""

from dataclasses import dataclass
from enum import Enum, auto

import numpy as np

from specter.domain.streams import SamplingConfig
from specter.domain.vision import Frame

_DOWNSCALE_TO = 32
_DEFAULT_DECREASE_FACTOR = 0.5
_DEFAULT_INCREASE_FPS = 0.5


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
    def __init__(
        self,
        sampling: SamplingConfig,
        *,
        motion_min_delta: float = 2.0,
        decrease_factor: float = _DEFAULT_DECREASE_FACTOR,
        increase_fps: float = _DEFAULT_INCREASE_FPS,
    ) -> None:
        self._sampling = sampling
        self._decrease_factor = decrease_factor
        self._increase_fps = increase_fps
        self._effective_fps = sampling.target_fps
        self._min_gap = 1.0 / self._effective_fps
        self._motion_gating = sampling.motion_gating
        self._motion_min_delta = motion_min_delta
        self._next_ts: float | None = None
        self._ref: np.ndarray | None = None
        self.stats = SamplerStats()

    @property
    def effective_fps(self) -> float:
        return self._effective_fps

    def adjust_for_latency(self, p95_ms: float) -> None:
        """Call periodically with the pipeline's recent inference p95. Exceeding the
        per-frame budget at the current admit rate means the model can't keep up with
        it; recovering it a step at a time (rather than snapping back to target_fps
        the moment it's briefly under budget) avoids oscillating."""
        budget_ms = 1000.0 / self._effective_fps
        if p95_ms > budget_ms:
            new_fps = max(self._effective_fps * self._decrease_factor, self._sampling.min_fps)
        else:
            new_fps = min(self._effective_fps + self._increase_fps, self._sampling.target_fps)
        if new_fps != self._effective_fps:
            self._effective_fps = new_fps
            self._min_gap = 1.0 / new_fps

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
