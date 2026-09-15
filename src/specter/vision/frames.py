"""Decoded video frames."""

from dataclasses import dataclass
from datetime import datetime

import numpy as np
from numpy.typing import NDArray

type FrameImage = NDArray[np.uint8]


@dataclass(frozen=True, slots=True, eq=False)
class Frame:
    """One decoded frame of a camera, as a BGR image of shape (height, width, 3).

    The image is shared between processing steps and must not be modified in place.
    """

    camera_id: str
    sequence_number: int
    # The stream's own timestamp, which keeps rate decisions independent of network jitter.
    presentation_time_seconds: float
    captured_at: datetime
    image: FrameImage

    @property
    def width_pixels(self) -> int:
        """The image's width."""
        return int(self.image.shape[1])

    @property
    def height_pixels(self) -> int:
        """The image's height."""
        return int(self.image.shape[0])
