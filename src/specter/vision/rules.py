"""Evaluates a camera's zone occupancy and line crossing rules against its tracks."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from specter.entities.rules import (
    CrossingDirection,
    LineCrossingRule,
    Rule,
    ZoneOccupancyRule,
)
from specter.entities.zones import Zone
from specter.vision.detections import Track

type Point = tuple[float, float]
type RuleTrackKey = tuple[str, int]


@dataclass(frozen=True, slots=True)
class RuleFiring:
    """A rule that fired for a track in the current frame."""

    rule: Rule
    track: Track
    zone_id: str | None = None
    dwell_seconds: float | None = None
    crossing_direction: CrossingDirection | None = None


class RuleEngine:
    """Fires a camera's rules as its tracks move, once per stay in a zone and once per crossing.

    An object stands where its box touches the ground, so every rule looks at the middle of the
    box's bottom edge, relative to the frame. Times are the stream's presentation times.
    """

    def __init__(self) -> None:
        self._zones_by_id: dict[str, Zone] = {}
        self._rules: tuple[Rule, ...] = ()
        self._entered_at_seconds: dict[RuleTrackKey, float] = {}
        self._fired_stays: set[RuleTrackKey] = set()
        self._last_points: dict[RuleTrackKey, Point] = {}

    def configure(self, zones: Sequence[Zone], rules: Sequence[Rule]) -> None:
        """Replaces the zones and rules, keeping the progress of rules that remain."""
        self._zones_by_id = {zone.id: zone for zone in zones}
        self._rules = tuple(rules)
        rule_ids = {rule.id for rule in rules}
        for progress in (self._entered_at_seconds, self._last_points):
            for key in [key for key in progress if key[0] not in rule_ids]:
                del progress[key]
        self._fired_stays = {key for key in self._fired_stays if key[0] in rule_ids}

    def evaluate(
        self,
        tracks: Iterable[Track],
        frame_width_pixels: int,
        frame_height_pixels: int,
        now_seconds: float,
    ) -> list[RuleFiring]:
        """Returns the rules that fired in this frame."""
        firings: list[RuleFiring] = []
        for track in tracks:
            point = locate_ground_point(track, frame_width_pixels, frame_height_pixels)
            for rule in self._rules:
                if not rule.is_enabled or (
                    rule.object_classes and track.detection.object_class not in rule.object_classes
                ):
                    continue
                match rule:
                    case ZoneOccupancyRule():
                        firing = self._evaluate_zone_occupancy(rule, track, point, now_seconds)
                    case LineCrossingRule():
                        firing = self._evaluate_line_crossing(rule, track, point)
                if firing is not None:
                    firings.append(firing)
        return firings

    def forget_tracks(self, track_ids: Iterable[int]) -> None:
        """Drops the progress of tracks that ended."""
        ended_track_ids = set(track_ids)
        for progress in (self._entered_at_seconds, self._last_points):
            for key in [key for key in progress if key[1] in ended_track_ids]:
                del progress[key]
        self._fired_stays = {key for key in self._fired_stays if key[1] not in ended_track_ids}

    def _evaluate_zone_occupancy(
        self, rule: ZoneOccupancyRule, track: Track, point: Point, now_seconds: float
    ) -> RuleFiring | None:
        zone = self._zones_by_id.get(rule.zone_id)
        key = (rule.id, track.track_id)
        if zone is None or not is_inside_polygon(
            point, [(corner.x, corner.y) for corner in zone.polygon]
        ):
            # Leaving the zone ends the stay, so coming back later fires the rule again.
            self._entered_at_seconds.pop(key, None)
            self._fired_stays.discard(key)
            return None
        entered_at_seconds = self._entered_at_seconds.setdefault(key, now_seconds)
        dwell_seconds = now_seconds - entered_at_seconds
        if key in self._fired_stays or dwell_seconds < rule.minimum_dwell_seconds:
            return None
        self._fired_stays.add(key)
        return RuleFiring(rule=rule, track=track, zone_id=zone.id, dwell_seconds=dwell_seconds)

    def _evaluate_line_crossing(
        self, rule: LineCrossingRule, track: Track, point: Point
    ) -> RuleFiring | None:
        key = (rule.id, track.track_id)
        previous_point = self._last_points.get(key)
        self._last_points[key] = point
        if previous_point is None:
            return None
        crossing_direction = find_crossing_direction(
            previous_point,
            point,
            (rule.line_start.x, rule.line_start.y),
            (rule.line_end.x, rule.line_end.y),
        )
        if crossing_direction is None or rule.direction not in (
            crossing_direction,
            CrossingDirection.EITHER,
        ):
            return None
        return RuleFiring(rule=rule, track=track, crossing_direction=crossing_direction)


def locate_ground_point(track: Track, frame_width_pixels: int, frame_height_pixels: int) -> Point:
    """Returns the middle of the track's box bottom edge, as fractions of the frame."""
    box = track.detection.bounding_box
    return (
        min(max((box.x + box.width / 2) / frame_width_pixels, 0.0), 1.0),
        min(max(box.bottom / frame_height_pixels, 0.0), 1.0),
    )


def is_inside_polygon(point: Point, polygon: Sequence[Point]) -> bool:
    """Whether the point lies inside the polygon, by counting edges that a ray from it crosses."""
    point_x, point_y = point
    is_inside = False
    for index, (start_x, start_y) in enumerate(polygon):
        end_x, end_y = polygon[index - 1]
        if (start_y > point_y) == (end_y > point_y):
            continue
        edge_x_at_point_height = start_x + (point_y - start_y) * (end_x - start_x) / (
            end_y - start_y
        )
        if point_x < edge_x_at_point_height:
            is_inside = not is_inside
    return is_inside


def find_crossing_direction(
    previous_point: Point, point: Point, line_start: Point, line_end: Point
) -> CrossingDirection | None:
    """Returns the direction in which moving between the points crossed the line, if it did.

    Directions are seen from the line's start looking toward its end, as the frame is displayed.
    """
    previous_side = _side_of_line(previous_point, line_start, line_end)
    current_side = _side_of_line(point, line_start, line_end)
    if previous_side * current_side >= 0:
        return None
    # The movement must also pass between the line's ends, not beside them.
    if (
        _side_of_line(line_start, previous_point, point)
        * _side_of_line(line_end, previous_point, point)
        > 0
    ):
        return None
    # Image rows grow downward, which puts the left side at a negative cross product.
    return CrossingDirection.LEFT_TO_RIGHT if previous_side < 0 else CrossingDirection.RIGHT_TO_LEFT


def _side_of_line(point: Point, line_start: Point, line_end: Point) -> float:
    return (line_end[0] - line_start[0]) * (point[1] - line_start[1]) - (
        line_end[1] - line_start[1]
    ) * (point[0] - line_start[0])
