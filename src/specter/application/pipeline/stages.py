"""Pure transforms between pipeline stages: detection filtering and track cropping."""

from collections.abc import Sequence

import numpy as np

from specter.domain.streams import StreamConfig
from specter.domain.vision import Crop, Detection, Frame, Track


def filter_detections(
    detections: Sequence[Detection], stream: StreamConfig, *, min_confidence: float
) -> list[Detection]:
    wanted = set(stream.detect_classes)
    return [
        d for d in detections if d.confidence >= min_confidence and (not wanted or d.cls in wanted)
    ]


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
