"""Bounding boxes and points in pixel and normalized coordinates."""

from dataclasses import dataclass

# Dividing pixel coordinates can land a hair above 1, which must not count as leaving the frame.
ROUNDING_TOLERANCE = 1e-9


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """An axis-aligned box in pixels, anchored at its top-left corner."""

    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError(f"bounding box needs a positive size, got {self.width}x{self.height}")

    @property
    def right(self) -> int:
        """The x coordinate just past the box's right edge."""
        return self.x + self.width

    @property
    def bottom(self) -> int:
        """The y coordinate just past the box's bottom edge."""
        return self.y + self.height

    @property
    def area(self) -> int:
        """The box's area in square pixels."""
        return self.width * self.height

    def clip_to_frame(self, frame_width: int, frame_height: int) -> "BoundingBox":
        """Returns the part of the box inside the frame, at least one pixel in size."""
        left = min(max(self.x, 0), max(frame_width - 1, 0))
        top = min(max(self.y, 0), max(frame_height - 1, 0))
        width = max(min(self.right, frame_width) - left, 1)
        height = max(min(self.bottom, frame_height) - top, 1)
        return BoundingBox(x=left, y=top, width=width, height=height)

    def iou(self, other: "BoundingBox") -> float:
        """Returns the intersection over union with another box, from 0 to 1."""
        intersection_width = max(min(self.right, other.right) - max(self.x, other.x), 0)
        intersection_height = max(min(self.bottom, other.bottom) - max(self.y, other.y), 0)
        intersection_area = intersection_width * intersection_height
        union_area = self.area + other.area - intersection_area
        return intersection_area / union_area


@dataclass(frozen=True, slots=True)
class NormalizedPoint:
    """A point as fractions of the frame's width and height, each from 0 to 1."""

    x: float
    y: float

    def __post_init__(self) -> None:
        if not (0.0 <= self.x <= 1.0 and 0.0 <= self.y <= 1.0):
            raise ValueError(f"normalized point must lie within 0 to 1, got ({self.x}, {self.y})")


@dataclass(frozen=True, slots=True)
class NormalizedBoundingBox:
    """A bounding box as fractions of the frame's width and height."""

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("normalized bounding box needs a positive size")
        if (
            self.x < 0
            or self.y < 0
            or self.x + self.width > 1 + ROUNDING_TOLERANCE
            or self.y + self.height > 1 + ROUNDING_TOLERANCE
        ):
            raise ValueError("normalized bounding box must lie within the frame")

    @classmethod
    def from_pixels(
        cls, bounding_box: BoundingBox, frame_width: int, frame_height: int
    ) -> "NormalizedBoundingBox":
        """Returns the box relative to the frame, after clipping it to the frame."""
        clipped_box = bounding_box.clip_to_frame(frame_width, frame_height)
        return cls(
            x=clipped_box.x / frame_width,
            y=clipped_box.y / frame_height,
            width=clipped_box.width / frame_width,
            height=clipped_box.height / frame_height,
        )
