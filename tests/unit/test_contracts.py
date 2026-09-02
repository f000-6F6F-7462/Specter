import json

import pytest
from pydantic import ValidationError

from specter.contracts import (
    MESSAGE_MODELS,
    SCHEMA_VERSION,
    MatchEventMessage,
    MessageType,
    parse_message,
)
from specter.contracts.export import write_json_schemas
from tests.conftest import make_match_event_message


def test_match_event_round_trips_through_json() -> None:
    original = make_match_event_message()
    wire = original.model_dump_json(by_alias=True)
    parsed = parse_message(wire)

    assert isinstance(parsed, MatchEventMessage)
    assert parsed == original
    assert parsed.type is MessageType.MATCH_EVENT
    assert parsed.schema_version == SCHEMA_VERSION


def test_detection_class_uses_the_class_alias_on_the_wire() -> None:
    payload = json.loads(make_match_event_message().model_dump_json(by_alias=True))
    assert payload["detection"]["class"] == "person"
    assert "object_class" not in payload["detection"]


def test_unknown_type_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown message type"):
        parse_message(
            {
                "type": "nope",
                "event_id": "x",
                "occurred_at": "2026-01-01T00:00:00Z",
                "owner_id": "o",
            }
        )


def test_extra_fields_are_forbidden() -> None:
    good = json.loads(make_match_event_message().model_dump_json(by_alias=True))
    good["surprise"] = 1
    with pytest.raises(ValidationError):
        MatchEventMessage.model_validate(good)


def test_similarity_bounds_are_enforced() -> None:
    with pytest.raises(ValidationError):
        make_match_event_message(
            match={
                "watchlist_id": "wl",
                "watchlist_name": "n",
                "kind": "blacklist",
                "target_id": "t",
                "target_label": "l",
                "target_type": "person",
                "similarity": 1.5,
                "threshold": 0.5,
            }
        )


def test_json_schema_export(tmp_path) -> None:
    written = write_json_schemas(tmp_path)
    assert {p.name for p in written} == {
        "match_event.schema.json",
        "stream_status.schema.json",
        "enrollment_status.schema.json",
        "enroll_job.schema.json",
    }
    for path in written:
        schema = json.loads(path.read_text())
        assert schema["type"] == "object"
        assert "properties" in schema


def test_registry_covers_every_message_type() -> None:
    assert set(MESSAGE_MODELS) == {
        "match_event",
        "stream_status",
        "enrollment_status",
        "enroll_job",
    }
