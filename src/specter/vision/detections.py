"""Objects found in a frame, and the tracks that follow them over time."""

from dataclasses import dataclass
from datetime import datetime

from specter.entities.geometry import BoundingBox


@dataclass(frozen=True, slots=True)
class Detection:
    """An object found in one frame."""

    object_class: str
    confidence_ratio: float
    bounding_box: BoundingBox

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence_ratio <= 1.0:
            raise ValueError(f"confidence must be between 0 and 1, got {self.confidence_ratio}")


@dataclass(frozen=True, slots=True)
class Track:
    """An object followed across frames under a stable id."""

    track_id: int
    detection: Detection
    first_seen_at: datetime
    age_frame_count: int = 0


@dataclass(frozen=True, slots=True)
class FaceDetection:
    """A face found in an image, with the five landmarks that face alignment needs."""

    confidence_ratio: float
    bounding_box: BoundingBox
    # Left eye, right eye, nose tip, left mouth corner and right mouth corner, as (x, y) pixels.
    landmarks: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence_ratio <= 1.0:
            raise ValueError(f"confidence must be between 0 and 1, got {self.confidence_ratio}")
