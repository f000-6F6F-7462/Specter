import pytest

from specter.core.errors import InvalidEntityError
from specter.entities.geometry import NormalizedPoint
from specter.entities.rules import (
    CrossingDirection,
    LineCrossingRule,
    RuleKind,
    ZoneOccupancyRule,
)
from specter.entities.zones import Zone


def test_zone_is_rejected_when_polygon_has_fewer_than_three_points() -> None:
    with pytest.raises(InvalidEntityError, match="at least 3 points"):
        Zone(
            id="zone_door",
            camera_id="camera_1",
            name="Door",
            polygon=(NormalizedPoint(0.1, 0.1), NormalizedPoint(0.5, 0.5)),
        )


def test_zone_occupancy_rule_is_rejected_when_dwell_is_negative() -> None:
    with pytest.raises(InvalidEntityError, match="minimum_dwell_seconds"):
        ZoneOccupancyRule(
            id="rule_1", zone_id="zone_door", object_classes=frozenset(), minimum_dwell_seconds=-1
        )


def test_line_crossing_rule_is_rejected_when_line_points_are_identical() -> None:
    point = NormalizedPoint(0.5, 0.5)

    with pytest.raises(InvalidEntityError, match="two different points"):
        LineCrossingRule(
            id="rule_1",
            camera_id="camera_1",
            line_start=point,
            line_end=point,
            direction=CrossingDirection.EITHER,
            object_classes=frozenset({"person"}),
        )


def test_each_rule_type_reports_its_kind_when_inspected() -> None:
    assert ZoneOccupancyRule.kind is RuleKind.ZONE_OCCUPANCY
    assert LineCrossingRule.kind is RuleKind.LINE_CROSSING
