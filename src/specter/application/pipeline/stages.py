"""Pure transforms between pipeline stages: detection filtering and track cropping."""

from collections.abc import Sequence

import numpy as np

from specter.domain.streams import RegionOfInterest, StreamConfig
from specter.domain.vision import BBox, Crop, Detection, Frame, Track


def filter_detections(
    detections: Sequence[Detection], stream: StreamConfig, frame: Frame, *, min_confidence: float
) -> list[Detection]:
    wanted = set(stream.detect_classes)
    candidates = [
        d for d in detections if d.confidence >= min_confidence and (not wanted or d.cls in wanted)
    ]
    if not stream.roi:
        return candidates
    height, width = frame.image.shape[0], frame.image.shape[1]
    rects = [_roi_pixels(r, width, height) for r in stream.roi]
    return [d for d in candidates if _center_in_any(d.bbox, rects)]


def _roi_pixels(
    roi: RegionOfInterest, width: int, height: int
) -> tuple[float, float, float, float]:
    return (roi.x * width, roi.y * height, (roi.x + roi.w) * width, (roi.y + roi.h) * height)


def _center_in_any(bbox: BBox, rects: Sequence[tuple[float, float, float, float]]) -> bool:
    cx, cy = bbox.x + bbox.w / 2, bbox.y + bbox.h / 2
    return any(x1 <= cx <= x2 and y1 <= cy <= y2 for x1, y1, x2, y2 in rects)


def crops_from_tracks(
    frame: Frame, tracks: Sequence[Track], *, modality: str
) -> list[tuple[Crop, Track]]:
    height, width = frame.image.shape[0], frame.image.shape[1]
    out: list[tuple[Crop, Track]] = []
    for track in tracks:
        box = track.detection.bbox.clip(width, height)
        patch = np.ascontiguousarray(frame.image[box.y : box.y + box.h, box.x : box.x + box.w])
        if patch.size == 0:
            continue
        out.append(
            (
                Crop(
                    stream_id=frame.stream_id,
                    track_id=track.track_id,
                    modality=modality,
                    image=patch,
                    quality=_sharpness(patch),
                ),
                track,
            )
        )
    return out


def _sharpness(patch: np.ndarray) -> float:
    """Cheap focus proxy in [0, 1]: normalised pixel variance."""
    variance = float(patch.astype(np.float32).var()) / 500.0
    return min(variance, 1.0)
