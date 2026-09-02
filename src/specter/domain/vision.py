"""Value objects that flow between pipeline stages.

Frames carry a single ``(H, W, 3)`` uint8 BGR array, passed by reference — stages
must not mutate it in place.
"""

from dataclasses import dataclass

import numpy as np

from specter.platform.errors import RuleViolation

type Vector = np.ndarray


@dataclass(frozen=True, slots=True)
class BBox:
    x: int
    y: int
    w: int
    h: int

    def __post_init__(self) -> None:
        if self.w <= 0 or self.h <= 0:
            raise RuleViolation(f"bbox needs positive size, got {self.w}x{self.h}")

    @property
    def x2(self) -> int:
        return self.x + self.w

    @property
    def y2(self) -> int:
        return self.y + self.h

    @property
    def area(self) -> int:
        return self.w * self.h

    def xyxy(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.x2, self.y2

    def clip(self, width: int, height: int) -> "BBox":
        x = min(max(self.x, 0), max(width - 1, 0))
        y = min(max(self.y, 0), max(height - 1, 0))
        w = max(min(self.x2, width) - x, 1)
        h = max(min(self.y2, height) - y, 1)
        return BBox(x, y, w, h)

    def iou(self, other: "BBox") -> float:
        ax1, ay1, ax2, ay2 = self.xyxy()
        bx1, by1, bx2, by2 = other.xyxy()
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        inter = max(ix2 - ix1, 0) * max(iy2 - iy1, 0)
        union = self.area + other.area - inter
        return inter / union if union else 0.0


@dataclass(frozen=True, slots=True, eq=False)
class Frame:
    stream_id: str
    seq: int
    ts: float
    image: np.ndarray

    @property
    def shape(self) -> tuple[int, ...]:
        return self.image.shape


@dataclass(frozen=True, slots=True)
class Detection:
    cls: str
    confidence: float
    bbox: BBox


@dataclass(frozen=True, slots=True)
class Track:
    track_id: int
    detection: Detection
    age: int = 0


@dataclass(frozen=True, slots=True, eq=False)
class Crop:
    stream_id: str
    track_id: int
    modality: str
    image: np.ndarray
    quality: float


@dataclass(frozen=True, slots=True, eq=False)
class Embedding:
    modality: str
    vector: Vector
    target_id: str | None = None
    image_id: str | None = None
    payload: dict[str, object] | None = None
