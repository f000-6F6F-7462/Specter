"""``specter bench`` — raw detect/embed throughput over a local video file.

No DB, bus, tracker or matcher involved: this answers "how fast do this machine's
configured models actually run" independent of camera/network conditions.
"""

import time
from dataclasses import dataclass

import numpy as np

from specter.application.ports import Detector, Embedder, FrameSource
from specter.domain.vision import Crop, Detection, Frame


@dataclass(frozen=True, slots=True)
class LatencyStats:
    count: int
    mean_ms: float
    p50_ms: float
    p95_ms: float
    max_ms: float

    @classmethod
    def of(cls, samples_ms: list[float]) -> "LatencyStats | None":
        if not samples_ms:
            return None
        ordered = sorted(samples_ms)
        n = len(ordered)
        return cls(
            count=n,
            mean_ms=sum(ordered) / n,
            p50_ms=ordered[n // 2],
            p95_ms=ordered[min(int(n * 0.95), n - 1)],
            max_ms=ordered[-1],
        )


@dataclass(frozen=True, slots=True)
class BenchReport:
    frames: int
    wall_s: float
    fps: float
    detect: LatencyStats | None
    embed: LatencyStats | None


def _crop_image(frame: Frame, detection: Detection) -> np.ndarray:
    box = detection.bbox.clip(frame.image.shape[1], frame.image.shape[0])
    return np.ascontiguousarray(frame.image[box.y : box.y + box.h, box.x : box.x + box.w])


async def run_bench(
    frames: FrameSource,
    detector: Detector,
    embedder: Embedder | None,
    *,
    max_frames: int | None = None,
) -> BenchReport:
    detect_ms: list[float] = []
    embed_ms: list[float] = []
    processed = 0
    start = time.monotonic()
    async for frame in frames:
        if max_frames is not None and processed >= max_frames:
            break
        t0 = time.monotonic()
        detections = (await detector.detect([frame]))[0]
        detect_ms.append((time.monotonic() - t0) * 1000.0)

        if embedder is not None and detections:
            crops = [
                Crop(
                    stream_id=frame.stream_id,
                    track_id=i,
                    modality=embedder.modality,
                    image=_crop_image(frame, d),
                    quality=1.0,
                )
                for i, d in enumerate(detections)
            ]
            t1 = time.monotonic()
            await embedder.embed(crops)
            embed_ms.append((time.monotonic() - t1) * 1000.0)

        processed += 1
    wall_s = time.monotonic() - start
    return BenchReport(
        frames=processed,
        wall_s=wall_s,
        fps=(processed / wall_s) if wall_s > 0 else 0.0,
        detect=LatencyStats.of(detect_ms),
        embed=LatencyStats.of(embed_ms),
    )
