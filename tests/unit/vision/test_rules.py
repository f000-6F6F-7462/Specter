from dataclasses import replace
from datetime import UTC, datetime

from specter.entities.geometry import BoundingBox, NormalizedPoint
from specter.entities.rules import CrossingDirection, LineCrossingRule, ZoneOccupancyRule
from specter.entities.zones import Zone
from specter.vision.detections import Detection, Track
from specter.vision.rules import RuleEngine, find_crossing_direction, is_inside_polygon

FRAME_WIDTH_PIXELS = 1000
FRAME_HEIGHT_PIXELS = 1000
FIRST_SEEN_AT = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
# The left half of the frame.
LEFT_HALF_ZONE = Zone(
    id="zone_left",
    camera_id="camera_front_door",
    name="Left half",
    polygon=(
        NormalizedPoint(0.0, 0.0),
        NormalizedPoint(0.5, 0.0),
        NormalizedPoint(0.5, 1.0),
        NormalizedPoint(0.0, 1.0),
    ),
)
# A vertical line down the middle, drawn from top to bottom.
MIDDLE_LINE_RULE = LineCrossingRule(
    id="rule_middle_line",
    camera_id="camera_front_door",
    line_start=NormalizedPoint(0.5, 0.0),
    line_end=NormalizedPoint(0.5, 1.0),
    direction=CrossingDirection.EITHER,
    object_classes=frozenset({"person"}),
)


def build_track(ground_x_pixels: int, *, track_id: int = 1, object_class: str = "person") -> Track:
    # The box's bottom edge touches the ground at the given x, halfway down the frame.
    return Track(
        track_id=track_id,
        detection=Detection(
            object_class=object_class,
            confidence_ratio=0.9,
            bounding_box=BoundingBox(x=ground_x_pixels - 50, y=300, width=100, height=200),
        ),
        first_seen_at=FIRST_SEEN_AT,
    )


def build_dwell_rule(minimum_dwell_seconds: float) -> ZoneOccupancyRule:
    return ZoneOccupancyRule(
        id="rule_left_dwell",
        zone_id=LEFT_HALF_ZONE.id,
        object_classes=frozenset({"person"}),
        minimum_dwell_seconds=minimum_dwell_seconds,
    )


def evaluate_rule_ids(engine: RuleEngine, track: Track, now_seconds: float) -> list[str]:
    firings = engine.evaluate([track], FRAME_WIDTH_PIXELS, FRAME_HEIGHT_PIXELS, now_seconds)
    return [firing.rule.id for firing in firings]


def test_zone_rule_fires_once_when_track_stays_past_minimum_dwell() -> None:
    engine = RuleEngine()
    engine.configure([LEFT_HALF_ZONE], [build_dwell_rule(minimum_dwell_seconds=2.0)])

    fired_rule_ids = [
        evaluate_rule_ids(engine, build_track(200), now_seconds) for now_seconds in (0, 1, 2, 3)
    ]

    assert fired_rule_ids == [[], [], ["rule_left_dwell"], []]


def test_zone_rule_fires_again_when_track_leaves_and_returns() -> None:
    engine = RuleEngine()
    engine.configure([LEFT_HALF_ZONE], [build_dwell_rule(minimum_dwell_seconds=0.0)])

    inside_first = evaluate_rule_ids(engine, build_track(200), 0)
    outside = evaluate_rule_ids(engine, build_track(800), 1)
    inside_again = evaluate_rule_ids(engine, build_track(200), 2)

    assert (inside_first, outside, inside_again) == (["rule_left_dwell"], [], ["rule_left_dwell"])


def test_zone_rule_ignores_track_when_its_class_is_not_watched() -> None:
    engine = RuleEngine()
    engine.configure([LEFT_HALF_ZONE], [build_dwell_rule(minimum_dwell_seconds=0.0)])

    assert evaluate_rule_ids(engine, build_track(200, object_class="car"), 0) == []


def test_line_rule_reports_direction_when_track_crosses_the_line() -> None:
    engine = RuleEngine()
    engine.configure([], [MIDDLE_LINE_RULE])

    evaluate_rule_ids(engine, build_track(300), 0)
    firings = engine.evaluate([build_track(700)], FRAME_WIDTH_PIXELS, FRAME_HEIGHT_PIXELS, 1)

    # Looking down the line from its top, the frame's right side is on the left.
    assert [firing.crossing_direction for firing in firings] == [CrossingDirection.RIGHT_TO_LEFT]


def test_line_rule_stays_quiet_when_direction_is_not_watched() -> None:
    engine = RuleEngine()
    engine.configure([], [replace(MIDDLE_LINE_RULE, direction=CrossingDirection.LEFT_TO_RIGHT)])

    evaluate_rule_ids(engine, build_track(300), 0)

    assert evaluate_rule_ids(engine, build_track(700), 1) == []


def test_line_progress_is_dropped_when_track_ends() -> None:
    engine = RuleEngine()
    engine.configure([], [MIDDLE_LINE_RULE])
    evaluate_rule_ids(engine, build_track(300), 0)

    engine.forget_tracks([1])

    assert evaluate_rule_ids(engine, build_track(700), 1) == []


def test_crossing_is_not_reported_when_movement_passes_beside_the_line() -> None:
    direction = find_crossing_direction((0.2, 0.8), (0.8, 0.8), (0.5, 0.0), (0.5, 0.5))

    assert direction is None


def test_point_is_inside_polygon_only_when_it_lies_within_its_edges() -> None:
    triangle = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]

    assert is_inside_polygon((0.2, 0.2), triangle)
    assert not is_inside_polygon((0.8, 0.8), triangle)


def test_person_still_in_zone_does_not_fire_again_when_stream_reconnects() -> None:
    engine = RuleEngine()
    engine.configure([LEFT_HALF_ZONE], [build_dwell_rule(minimum_dwell_seconds=0.0)])
    before_reconnect = evaluate_rule_ids(engine, build_track(200, track_id=1), 100.0)

    engine.carry_over_stays([1], now_seconds=0.0)
    after_reconnect = evaluate_rule_ids(engine, build_track(205, track_id=7), 0.5)

    assert (before_reconnect, after_reconnect) == (["rule_left_dwell"], [])


def test_dwell_continues_across_a_reconnect_when_the_person_stays() -> None:
    engine = RuleEngine()
    engine.configure([LEFT_HALF_ZONE], [build_dwell_rule(minimum_dwell_seconds=5.0)])
    evaluate_rule_ids(engine, build_track(200, track_id=1), 100.0)
    evaluate_rule_ids(engine, build_track(200, track_id=1), 103.0)

    engine.carry_over_stays([1], now_seconds=0.0)
    # The time the stream was down does not count as dwell.
    first_firings = engine.evaluate(
        [build_track(200, track_id=7)], FRAME_WIDTH_PIXELS, FRAME_HEIGHT_PIXELS, 0.5
    )
    later_firings = engine.evaluate(
        [build_track(200, track_id=7)], FRAME_WIDTH_PIXELS, FRAME_HEIGHT_PIXELS, 2.5
    )

    assert first_firings == []
    assert [firing.dwell_seconds for firing in later_firings] == [5.0]


def test_person_elsewhere_in_zone_fires_after_a_reconnect() -> None:
    engine = RuleEngine()
    engine.configure([LEFT_HALF_ZONE], [build_dwell_rule(minimum_dwell_seconds=0.0)])
    evaluate_rule_ids(engine, build_track(100, track_id=1), 100.0)

    engine.carry_over_stays([1], now_seconds=0.0)

    assert evaluate_rule_ids(engine, build_track(400, track_id=7), 0.5) == ["rule_left_dwell"]


def test_carried_stay_expires_when_nobody_appears_in_time() -> None:
    engine = RuleEngine()
    engine.configure([LEFT_HALF_ZONE], [build_dwell_rule(minimum_dwell_seconds=0.0)])
    evaluate_rule_ids(engine, build_track(200, track_id=1), 100.0)

    engine.carry_over_stays([1], now_seconds=0.0)

    assert evaluate_rule_ids(engine, build_track(200, track_id=7), 30.0) == ["rule_left_dwell"]
