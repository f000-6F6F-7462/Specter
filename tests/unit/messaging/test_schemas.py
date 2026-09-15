from pathlib import Path

from specter.messaging.messages import EnrollmentStatusChangedMessage
from specter.messaging.schemas import build_schema_file_name, render_json_schemas

SCHEMA_DIRECTORY = Path(__file__).resolve().parents[3] / "contracts" / "jsonschema"


def test_schema_file_name_is_snake_case_without_message_suffix_when_built() -> None:
    file_name = build_schema_file_name(EnrollmentStatusChangedMessage)

    assert file_name == "enrollment_status_changed.schema.json"


def test_committed_schemas_match_message_models_when_regenerated() -> None:
    for file_name, schema_text in render_json_schemas().items():
        schema_file = SCHEMA_DIRECTORY / file_name
        assert schema_file.exists(), f"{file_name} is missing: run make contracts"
        assert schema_file.read_text(encoding="utf-8") == schema_text, (
            f"{file_name} is out of date: run make contracts"
        )
