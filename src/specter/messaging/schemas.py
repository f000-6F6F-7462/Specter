"""Generates the JSON Schema files that document Specter's messages for external clients."""

import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from specter.messaging.messages import MESSAGE_TYPES, SpecterMessage

DEFAULT_SCHEMA_DIRECTORY = Path("contracts/jsonschema")
SCHEMA_FILE_SUFFIX = ".schema.json"
WORD_BOUNDARY_PATTERN = re.compile(r"(?<!^)(?=[A-Z])")


def build_schema_file_name(message_type: type[SpecterMessage]) -> str:
    """Returns the schema file name of a message type, such as ``match_confirmed.schema.json``."""
    base_name = message_type.__name__.removesuffix("Message")
    return f"{WORD_BOUNDARY_PATTERN.sub('_', base_name).lower()}{SCHEMA_FILE_SUFFIX}"


def render_json_schemas() -> dict[str, str]:
    """Returns the formatted JSON Schema of every message type, keyed by file name."""
    return {
        build_schema_file_name(message_type): json.dumps(
            message_type.model_json_schema(), indent=2, sort_keys=True
        )
        + "\n"
        for message_type in MESSAGE_TYPES
    }


def write_json_schemas(schema_directory: Path) -> list[Path]:
    """Writes every message type's JSON Schema into the directory and returns the files."""
    schema_directory.mkdir(parents=True, exist_ok=True)
    schema_files: list[Path] = []
    for file_name, schema_text in render_json_schemas().items():
        schema_file = schema_directory / file_name
        schema_file.write_text(schema_text, encoding="utf-8")
        schema_files.append(schema_file)
    return schema_files


def main(arguments: Sequence[str] | None = None) -> None:
    """Writes the schemas into the directory given on the command line, or the default one."""
    given_arguments = sys.argv[1:] if arguments is None else list(arguments)
    schema_directory = Path(given_arguments[0]) if given_arguments else DEFAULT_SCHEMA_DIRECTORY
    for schema_file in write_json_schemas(schema_directory):
        print(f"wrote {schema_file}")


if __name__ == "__main__":
    main()
