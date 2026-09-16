from datetime import UTC, datetime

from specter.camera_manager.alert_recorder import build_identity_match_alert, build_rule_alert
from specter.entities.alerts import Disposition
from specter.entities.rules import RuleKind
from specter.entities.targets import EmbeddingModality
from specter.messaging.messages import (
    MatchConfirmedMessage,
    MessageBoundingBox,
    RuleTriggeredMessage,
)

OCCURRED_AT = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
BOUNDING_BOX = MessageBoundingBox(x=0.25, y=0.1, width=0.2, height=0.6)


def test_match_alert_takes_the_event_message_id_when_built() -> None:
    message = MatchConfirmedMessage(
        occurred_at=OCCURRED_AT,
        owner_id="owner_alice",
        camera_id="camera_front_door",
        track_id=17,
        watchlist_id="watchlist_wanted",
        target_id="target_jane",
        modality=EmbeddingModality.APPEARANCE,
        similarity_ratio=0.86,
        margin_ratio=0.31,
        threshold_ratio=0.78,
        object_class="person",
        bounding_box=BOUNDING_BOX,
        first_seen_at=OCCURRED_AT,
        frame_captured_at=OCCURRED_AT,
        snapshot_path="evidence/owner_alice/2026/09/16/alert.jpg",
    )

    alert = build_identity_match_alert(message)

    assert alert.id == message.message_id
    assert alert.modality is EmbeddingModality.APPEARANCE
    assert alert.snapshot_path == message.snapshot_path
    assert alert.review.disposition is Disposition.UNREVIEWED


def test_rule_alert_keeps_the_zone_and_dwell_when_built() -> None:
    message = RuleTriggeredMessage(
        occurred_at=OCCURRED_AT,
        owner_id="owner_alice",
        camera_id="camera_front_door",
        rule_id="rule_door_dwell",
        rule_kind=RuleKind.ZONE_OCCUPANCY,
        zone_id="zone_door",
        track_id=4,
        object_class="car",
        bounding_box=BOUNDING_BOX,
        dwell_seconds=12.5,
        frame_captured_at=OCCURRED_AT,
    )

    alert = build_rule_alert(message)

    assert (alert.id, alert.zone_id, alert.dwell_seconds) == (
        message.message_id,
        "zone_door",
        12.5,
    )
