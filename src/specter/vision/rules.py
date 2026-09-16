"""Evaluates a camera's zone occupancy and line crossing rules against its tracks."""

import math
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

# A stream that reconnects starts its tracks over; an object found this close to where a stay
# ended, as a fraction of the frame, within this long, is taken to be the same object still there.
CARRIED_STAY_MAXIMUM_DISTANCE_RATIO = 0.05
CARRIED_STAY_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class RuleFiring:
    """A rule that fired for a track in the current frame."""

    rule: Rule
    track: Track
    zone_id: str | None = None
    dwell_seconds: float | None = None
    crossing_direction: CrossingDirection | None = None


@dataclass(frozen=True, slots=True)
class _ZoneStay:
    entered_at_seconds: float
    last_seen_at_seconds: float
    last_point: Point
    has_fired: bool


@dataclass(frozen=True, slots=True)
class _CarriedStay:
    rule_id: str
    point: Point
    dwell_seconds: float
    has_fired: bool
    expires_at_seconds: float


class RuleEngine:
    """Fires a camera's rules as its tracks move, once per stay in a zone and once per crossing.

    An object stands where its box touches the ground, so every rule looks at the middle of the
    box's bottom edge, relative to the frame. Times are the stream's presentation times. When the
    stream reconnects, stays are carried over to the objects found where they ended, so people who
    never left a zone do not fire its rule again.
    """

    def __init__(self) -> None:
        self._zones_by_id: dict[str, Zone] = {}
        self._rules: tuple[Rule, ...] = ()
        self._zone_stays: dict[RuleTrackKey, _ZoneStay] = {}
        self._carried_stays: list[_CarriedStay] = []
        self._last_points: dict[RuleTrackKey, Point] = {}

    def configure(self, zones: Sequence[Zone], rules: Sequence[Rule]) -> None:
        """Replaces the zones and rules, keeping the progress of rules that remain."""
        self._zones_by_id = {zone.id: zone for zone in zones}
        self._rules = tuple(rules)
        rule_ids = {rule.id for rule in rules}
        self._zone_stays = {
            key: stay for key, stay in self._zone_stays.items() if key[0] in rule_ids
        }
        self._last_points = {
            key: point for key, point in self._last_points.items() if key[0] in rule_ids
        }
        self._carried_stays = [stay for stay in self._carried_stays if stay.rule_id in rule_ids]

    def evaluate(
        self,
        tracks: Iterable[Track],
        frame_width_pixels: int,
        frame_height_pixels: int,
        now_seconds: float,
    ) -> list[RuleFiring]:
        """Returns the rules that fired in this frame."""
        self._carried_stays = [
            stay for stay in self._carried_stays if stay.expires_at_seconds >= now_seconds
        ]
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
        self._zone_stays = {
            key: stay for key, stay in self._zone_stays.items() if key[1] not in ended_track_ids
        }
        self._last_points = {
            key: point for key, point in self._last_points.items() if key[1] not in ended_track_ids
        }

    def carry_over_stays(self, track_ids: Iterable[int], now_seconds: float) -> None:
        """Keeps the zone stays of tracks that ended because the stream started over.

        Args:
            now_seconds: The first timestamp of the restarted stream, which begins a new timeline.
        """
        ended_track_ids = set(track_ids)
        for (rule_id, track_id), stay in self._zone_stays.items():
            if track_id in ended_track_ids:
                self._carried_stays.append(
                    _CarriedStay(
                        rule_id=rule_id,
                        point=stay.last_point,
                        dwell_seconds=stay.last_seen_at_seconds - stay.entered_at_seconds,
                        has_fired=stay.has_fired,
                        expires_at_seconds=now_seconds + CARRIED_STAY_SECONDS,
                    )
                )
        self.forget_tracks(ended_track_ids)

    def _evaluate_zone_occupancy(
        self, rule: ZoneOccupancyRule, track: Track, point: Point, now_seconds: float
    ) -> RuleFiring | None:
        zone = self._zones_by_id.get(rule.zone_id)
        key = (rule.id, track.track_id)
        if zone is None or not is_inside_polygon(
            point, [(corner.x, corner.y) for corner in zone.polygon]
        ):
            # Leaving the zone ends the stay, so coming back later fires the rule again.
            self._zone_stays.pop(key, None)
            return None
        stay = self._zone_stays.get(key) or self._start_stay(rule.id, point, now_seconds)
        dwell_seconds = now_seconds - stay.entered_at_seconds
        is_firing = not stay.has_fired and dwell_seconds >= rule.minimum_dwell_seconds
        self._zone_stays[key] = _ZoneStay(
            entered_at_seconds=stay.entered_at_seconds,
            last_seen_at_seconds=now_seconds,
            last_point=point,
            has_fired=stay.has_fired or is_firing,
        )
        if not is_firing:
            return None
        return RuleFiring(rule=rule, track=track, zone_id=zone.id, dwell_seconds=dwell_seconds)

    def _start_stay(self, rule_id: str, point: Point, now_seconds: float) -> _ZoneStay:
        carried_stay = min(
            (
                stay
                for stay in self._carried_stays
                if stay.rule_id == rule_id
                and math.dist(stay.point, point) <= CARRIED_STAY_MAXIMUM_DISTANCE_RATIO
            ),
            key=lambda stay: math.dist(stay.point, point),
            default=None,
        )
        if carried_stay is None:
            return _ZoneStay(
                entered_at_seconds=now_seconds,
                last_seen_at_seconds=now_seconds,
                last_point=point,
                has_fired=False,
            )
        # Each carried stay belongs to one object, so a second object nearby starts its own stay.
        self._carried_stays.remove(carried_stay)
        return _ZoneStay(
            entered_at_seconds=now_seconds - carried_stay.dwell_seconds,
            last_seen_at_seconds=now_seconds,
            last_point=point,
            has_fired=carried_stay.has_fired,
        )

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
