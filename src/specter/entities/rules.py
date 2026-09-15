"""Rules that raise alerts about objects, independent of who or what they are."""

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from specter.core.errors import InvalidEntityError
from specter.entities.geometry import NormalizedPoint
from specter.entities.validation import require_non_negative


class RuleKind(StrEnum):
    """Which kind of rule fired."""

    ZONE_OCCUPANCY = "zone_occupancy"
    LINE_CROSSING = "line_crossing"


class CrossingDirection(StrEnum):
    """Direction of travel across a line, as seen from the line's start looking toward its end."""

    LEFT_TO_RIGHT = "left_to_right"
    RIGHT_TO_LEFT = "right_to_left"
    EITHER = "either"


@dataclass(frozen=True, slots=True)
class ZoneOccupancyRule:
    """Fires when an object of a watched class stays in a zone long enough."""

    kind: ClassVar[RuleKind] = RuleKind.ZONE_OCCUPANCY

    id: str
    zone_id: str
    # An empty set means objects of every class.
    object_classes: frozenset[str]
    minimum_dwell_seconds: float = 0.0
    is_enabled: bool = True

    def __post_init__(self) -> None:
        require_non_negative(self.minimum_dwell_seconds, "minimum_dwell_seconds")


@dataclass(frozen=True, slots=True)
class LineCrossingRule:
    """Fires when an object of a watched class crosses a line in a given direction."""

    kind: ClassVar[RuleKind] = RuleKind.LINE_CROSSING

    id: str
    camera_id: str
    line_start: NormalizedPoint
    line_end: NormalizedPoint
    direction: CrossingDirection
    # An empty set means objects of every class.
    object_classes: frozenset[str]
    is_enabled: bool = True

    def __post_init__(self) -> None:
        if self.line_start == self.line_end:
            raise InvalidEntityError("a crossing line needs two different points")


type Rule = ZoneOccupancyRule | LineCrossingRule
