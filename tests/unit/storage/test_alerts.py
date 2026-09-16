from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from specter.core.errors import NotFoundError
from specter.entities.alerts import AlertReview, Disposition, IdentityMatchAlert, RuleAlert
from specter.entities.geometry import NormalizedBoundingBox
from specter.entities.rules import CrossingDirection, RuleKind
from specter.entities.targets import EmbeddingModality
from specter.storage.alerts import (
    AlertFilter,
    AlertPosition,
    clear_snapshot_paths_under,
    find_alert,
    list_identity_match_alerts,
    save_alert,
    save_alert_review,
)

pytestmark = pytest.mark.usefixtures("database")

OWNER_ID = "owner_alice"
FIRST_ALERT_AT = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
BOUNDING_BOX = NormalizedBoundingBox(x=0.25, y=0.1, width=0.2, height=0.6)


def build_identity_match_alert(
    alert_id: str, *, minutes_after_first: int = 0, camera_id: str = "camera_front_door"
) -> IdentityMatchAlert:
    created_at = FIRST_ALERT_AT + timedelta(minutes=minutes_after_first)
    return IdentityMatchAlert(
        id=alert_id,
        owner_id=OWNER_ID,
        camera_id=camera_id,
        track_id=17,
        watchlist_id="watchlist_visitors",
        target_id="target_jane",
        modality=EmbeddingModality.FACE,
        similarity_ratio=0.86,
        margin_ratio=0.31,
        object_class="person",
        bounding_box=BOUNDING_BOX,
        frame_captured_at=created_at,
        created_at=created_at,
        snapshot_path=f"evidence/{OWNER_ID}/2026/09/15/{alert_id}.jpg",
    )


def test_identity_match_alert_is_unchanged_when_saved_and_loaded() -> None:
    alert = build_identity_match_alert("alert_1")

    save_alert(alert)

    assert find_alert("alert_1") == alert


def test_alert_is_saved_once_when_the_same_alert_is_saved_again() -> None:
    alert = build_identity_match_alert("alert_redelivered")

    save_alert(alert)
    save_alert(replace(alert, similarity_ratio=0.5))

    assert find_alert("alert_redelivered") == alert


def test_rule_alert_is_unchanged_when_saved_and_loaded() -> None:
    alert = RuleAlert(
        id="alert_line",
        owner_id=OWNER_ID,
        camera_id="camera_front_door",
        track_id=4,
        rule_id="rule_entrance",
        rule_kind=RuleKind.LINE_CROSSING,
        zone_id=None,
        object_class="car",
        bounding_box=BOUNDING_BOX,
        dwell_seconds=None,
        crossing_direction=CrossingDirection.LEFT_TO_RIGHT,
        frame_captured_at=FIRST_ALERT_AT,
        created_at=FIRST_ALERT_AT,
    )

    save_alert(alert)

    assert find_alert("alert_line") == alert


def test_review_is_stored_when_alert_is_resolved() -> None:
    save_alert(build_identity_match_alert("alert_1"))
    review = AlertReview().resolve(Disposition.FALSE_POSITIVE, note="delivery driver")

    save_alert_review("alert_1", review)

    reviewed_alert = find_alert("alert_1")
    assert reviewed_alert is not None
    assert reviewed_alert.review == review


def test_reviewing_fails_when_alert_does_not_exist() -> None:
    with pytest.raises(NotFoundError):
        save_alert_review("alert_missing", AlertReview().acknowledge())


def test_alerts_are_listed_newest_first_in_pages_when_paging_backwards() -> None:
    for minute in range(5):
        save_alert(build_identity_match_alert(f"alert_{minute}", minutes_after_first=minute))

    first_page = list_identity_match_alerts(OWNER_ID, AlertFilter(), limit=2)
    last_alert_of_first_page = first_page[-1]
    second_page = list_identity_match_alerts(
        OWNER_ID,
        AlertFilter(
            older_than=AlertPosition(
                created_at=last_alert_of_first_page.created_at,
                alert_id=last_alert_of_first_page.id,
            )
        ),
        limit=2,
    )

    assert [alert.id for alert in first_page] == ["alert_4", "alert_3"]
    assert [alert.id for alert in second_page] == ["alert_2", "alert_1"]


def test_only_matching_camera_is_listed_when_filtering_by_camera() -> None:
    save_alert(build_identity_match_alert("alert_door", camera_id="camera_front_door"))
    save_alert(build_identity_match_alert("alert_garage", camera_id="camera_garage"))

    garage_alerts = list_identity_match_alerts(
        OWNER_ID, AlertFilter(camera_id="camera_garage"), limit=10
    )

    assert [alert.id for alert in garage_alerts] == ["alert_garage"]


def test_snapshot_paths_are_cleared_only_under_removed_day_when_retention_runs() -> None:
    save_alert(build_identity_match_alert("alert_1"))
    save_alert(
        replace(
            build_identity_match_alert("alert_2"),
            snapshot_path=f"evidence/{OWNER_ID}/2026/09/16/alert_2.jpg",
        )
    )

    cleared_alert_count = clear_snapshot_paths_under(f"evidence/{OWNER_ID}/2026/09/15")

    assert cleared_alert_count == 1
    cleared_alert = find_alert("alert_1")
    kept_alert = find_alert("alert_2")
    assert cleared_alert is not None
    assert kept_alert is not None
    assert cleared_alert.snapshot_path is None
    assert kept_alert.snapshot_path is not None
