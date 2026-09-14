"""Golden-file tests for the cross-team event contract (docs/ARCHITECTURE.md §7.3).

Two things can go wrong that ``tests/unit/test_contracts.py`` doesn't catch:

1. Someone changes a message model in ``contracts/messages.py`` and forgets to run
   ``make contracts`` — the checked-in schema under ``contracts/jsonschema/`` silently
   drifts from what the models actually produce. ``test_schema_matches_checked_in_file``
   catches that by byte-comparing against the checked-in file, not a freshly-written one.
2. The generated schema doesn't actually accept a real instance of its own message —
   ``test_example_validates_against_its_schema`` round-trips one realistic example per
   message type through a real JSON Schema validator (not just Pydantic's own).
"""

import json
from pathlib import Path

import jsonschema
import pytest

from specter.contracts import BrokerMessage, MessageType
from tests.conftest import (
    make_enroll_job_message,
    make_enrollment_status_message,
    make_match_event_message,
    make_stream_status_message,
)

_JSONSCHEMA_DIR = Path(__file__).resolve().parents[2] / "contracts" / "jsonschema"

_EXAMPLES: dict[MessageType, BrokerMessage] = {
    MessageType.MATCH_EVENT: make_match_event_message(),
    MessageType.STREAM_STATUS: make_stream_status_message(),
    MessageType.ENROLLMENT_STATUS: make_enrollment_status_message(),
    MessageType.ENROLL_JOB: make_enroll_job_message(),
}


@pytest.mark.parametrize("message_type", list(MessageType), ids=lambda t: t.value)
class TestEventSchemaContract:
    def test_schema_matches_checked_in_file(self, message_type: MessageType) -> None:
        example = _EXAMPLES[message_type]
        generated = type(example).model_json_schema(by_alias=True)
        checked_in_path = _JSONSCHEMA_DIR / f"{message_type.value}.schema.json"
        checked_in = json.loads(checked_in_path.read_text())
        assert generated == checked_in, (
            f"{checked_in_path} is stale — the {type(example).__name__} model changed "
            "without regenerating it. Run `make contracts`."
        )

    def test_example_validates_against_its_schema(self, message_type: MessageType) -> None:
        example = _EXAMPLES[message_type]
        schema = json.loads((_JSONSCHEMA_DIR / f"{message_type.value}.schema.json").read_text())
        instance = json.loads(example.model_dump_json(by_alias=True))
        jsonschema.validate(instance=instance, schema=schema)


def test_every_message_type_has_a_golden_example() -> None:
    assert set(_EXAMPLES) == set(MessageType)
