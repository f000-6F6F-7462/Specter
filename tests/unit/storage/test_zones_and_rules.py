import pytest

from specter.core.errors import InvalidEntityError, NotFoundError
from specter.entities.cameras import Camera
from specter.entities.geometry import NormalizedPoint
from specter.entities.rules import CrossingDirection, LineCrossingRule, ZoneOccupancyRule
from specter.entities.zones import Zone
from specter.storage.cameras import save_camera
from specter.storage.rules import delete_rule, find_rule, list_camera_rules, save_rule
from specter.storage.zones import delete_zone, get_zone, save_zone

# The database comes first, so the camera is saved into this test's own database.
pytestmark = pytest.mark.usefixtures("database", "front_door_camera")

CAMERA_ID = "camera_front_door"
DOOR_ZONE = Zone(
    id="zone_door",
    camera_id=CAMERA_ID,
    name="Door",
    polygon=(NormalizedPoint(0.1, 0.2), NormalizedPoint(0.6, 0.2), NormalizedPoint(0.4, 0.9)),
)
LOITERING_RULE = ZoneOccupancyRule(
    id="rule_loitering",
    zone_id="zone_door",
    object_classes=frozenset({"person"}),
    minimum_dwell_seconds=30.0,
)
ENTRANCE_LINE_RULE = LineCrossingRule(
    id="rule_entrance",
    camera_id=CAMERA_ID,
    line_start=NormalizedPoint(0.0, 0.5),
    line_end=NormalizedPoint(1.0, 0.5),
    direction=CrossingDirection.LEFT_TO_RIGHT,
    object_classes=frozenset({"person", "car"}),
)


@pytest.fixture
def front_door_camera() -> None:
    save_camera(
        Camera(id=CAMERA_ID, owner_id="owner_alice", name="Front door", source_url="rtsp://door"),
        None,
    )


def test_zone_is_unchanged_when_saved_and_loaded() -> None:
    save_zone(DOOR_ZONE)

    assert get_zone("zone_door") == DOOR_ZONE


def test_zone_is_rejected_when_its_camera_does_not_exist() -> None:
    orphan_zone = Zone(
        id="zone_orphan", camera_id="camera_missing", name="Orphan", polygon=DOOR_ZONE.polygon
    )

    with pytest.raises(InvalidEntityError, match="camera that does not exist"):
        save_zone(orphan_zone)


def test_camera_rules_of_both_kinds_are_listed_when_saved() -> None:
    save_zone(DOOR_ZONE)
    save_rule(LOITERING_RULE)
    save_rule(ENTRANCE_LINE_RULE)

    assert list_camera_rules(CAMERA_ID) == [LOITERING_RULE, ENTRANCE_LINE_RULE]


def test_occupancy_rule_is_deleted_when_its_zone_is_deleted() -> None:
    save_zone(DOOR_ZONE)
    save_rule(LOITERING_RULE)

    delete_zone("zone_door")

    assert find_rule("rule_loitering") is None


def test_rule_is_rejected_when_its_zone_does_not_exist() -> None:
    with pytest.raises(InvalidEntityError, match="zone or camera that does not exist"):
        save_rule(LOITERING_RULE)


def test_deleting_fails_when_rule_does_not_exist() -> None:
    with pytest.raises(NotFoundError):
        delete_rule("rule_missing")
