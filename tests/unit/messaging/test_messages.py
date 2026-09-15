from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from specter.entities.cameras import CameraStatus
from specter.messaging.messages import (
    CameraStatusChangedMessage,
    MatchConfirmedMessage,
    MessageBoundingBox,
)

OCCURRED_AT = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def build_match_confirmed_message() -> MatchConfirmedMessage:
    return MatchConfirmedMessage(
        occurred_at=OCCURRED_AT,
        owner_id="owner_alice",
        camera_id="camera_front_door",
        track_id=17,
        watchlist_id="watchlist_visitors",
        target_id="target_jane",
        similarity_ratio=0.86,
        margin_ratio=0.31,
        threshold_ratio=0.78,
        object_class="person",
        bounding_box=MessageBoundingBox(x=0.25, y=0.1, width=0.2, height=0.6),
        first_seen_at=OCCURRED_AT,
        frame_captured_at=OCCURRED_AT,
    )


def test_message_is_unchanged_when_serialized_and_parsed_back() -> None:
    message = build_match_confirmed_message()

    parsed_message = MatchConfirmedMessage.model_validate_json(message.model_dump_json())

    assert parsed_message == message


def test_message_is_rejected_when_it_contains_an_unknown_field() -> None:
    payload = build_match_confirmed_message().model_dump(mode="json") | {"confidence": 0.9}

    with pytest.raises(ValidationError, match="extra"):
        MatchConfirmedMessage.model_validate(payload)


def test_message_is_rejected_when_time_has_no_timezone() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        CameraStatusChangedMessage(
            occurred_at=datetime(2026, 9, 15, 12, 0),
            owner_id="owner_alice",
            camera_id="camera_front_door",
            status=CameraStatus.RUNNING,
        )


def test_each_message_gets_its_own_id_when_created() -> None:
    first_message = build_match_confirmed_message()
    second_message = build_match_confirmed_message()

    assert first_message.message_id.startswith("message_")
    assert first_message.message_id != second_message.message_id
