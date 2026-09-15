from dataclasses import replace
from datetime import UTC, datetime

import pytest

from specter.core.errors import InvalidEntityError
from specter.entities.alerts import AlertReview, Disposition, IdentityMatchAlert, RuleAlert
from specter.entities.rules import RuleKind
from specter.vision.geometry import NormalizedBoundingBox

CREATED_AT = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
BOUNDING_BOX = NormalizedBoundingBox(x=0.25, y=0.1, width=0.2, height=0.6)


def build_identity_match_alert() -> IdentityMatchAlert:
    return IdentityMatchAlert(
        id="alert_1",
        owner_id="owner_alice",
        camera_id="camera_front_door",
        track_id=17,
        watchlist_id="watchlist_visitors",
        target_id="target_jane",
        similarity_ratio=0.86,
        margin_ratio=0.31,
        object_class="person",
        bounding_box=BOUNDING_BOX,
        frame_captured_at=CREATED_AT,
        created_at=CREATED_AT,
    )


def test_new_alert_is_unreviewed_when_created() -> None:
    review = build_identity_match_alert().review

    assert review.disposition is Disposition.UNREVIEWED
    assert not review.is_acknowledged


def test_resolving_acknowledges_alert_when_verdict_is_given() -> None:
    alert = build_identity_match_alert()

    reviewed_alert = replace(
        alert, review=alert.review.resolve(Disposition.FALSE_POSITIVE, note="delivery driver")
    )

    assert reviewed_alert.review == AlertReview(
        disposition=Disposition.FALSE_POSITIVE, is_acknowledged=True, note="delivery driver"
    )
    assert alert.review.disposition is Disposition.UNREVIEWED


def test_resolving_is_rejected_when_verdict_is_unreviewed() -> None:
    with pytest.raises(InvalidEntityError, match="unreviewed"):
        AlertReview().resolve(Disposition.UNREVIEWED)


def test_zone_occupancy_alert_is_rejected_when_zone_is_missing() -> None:
    with pytest.raises(InvalidEntityError, match="zone_id"):
        RuleAlert(
            id="alert_2",
            owner_id="owner_alice",
            camera_id="camera_front_door",
            track_id=4,
            rule_id="rule_loitering",
            rule_kind=RuleKind.ZONE_OCCUPANCY,
            zone_id=None,
            object_class="person",
            bounding_box=BOUNDING_BOX,
            dwell_seconds=30.0,
            crossing_direction=None,
            frame_captured_at=CREATED_AT,
            created_at=CREATED_AT,
        )
