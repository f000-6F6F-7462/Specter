"""Zones: named areas of a camera's view."""

from dataclasses import dataclass

from specter.core.errors import InvalidEntityError
from specter.entities.validation import require_non_empty_text
from specter.vision.geometry import NormalizedPoint

MINIMUM_POLYGON_POINT_COUNT = 3


@dataclass(frozen=True, slots=True)
class Zone:
    """A named area of one camera's view, drawn as a polygon."""

    id: str
    camera_id: str
    name: str
    polygon: tuple[NormalizedPoint, ...]

    def __post_init__(self) -> None:
        require_non_empty_text(self.name, "zone name")
        if len(self.polygon) < MINIMUM_POLYGON_POINT_COUNT:
            raise InvalidEntityError(
                f"zone polygon needs at least {MINIMUM_POLYGON_POINT_COUNT} points, "
                f"got {len(self.polygon)}"
            )
