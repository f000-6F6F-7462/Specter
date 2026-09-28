"""Generates the OpenAPI document that describes Specter's HTTP API for external clients."""

import json
import sys
from collections.abc import Sequence
from pathlib import Path

from specter.api.main import create_app
from specter.config.settings import Settings

DEFAULT_OPENAPI_FILE = Path("contracts/openapi.json")


def render_openapi_document() -> str:
    """Returns the formatted OpenAPI document of the HTTP API."""
    # Building the app opens nothing: every service starts in the lifespan, which never runs here.
    document = create_app(Settings()).openapi()
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(arguments: Sequence[str] | None = None) -> None:
    """Writes the document to the file given on the command line, or the default one."""
    given_arguments = sys.argv[1:] if arguments is None else list(arguments)
    openapi_file = Path(given_arguments[0]) if given_arguments else DEFAULT_OPENAPI_FILE
    openapi_file.parent.mkdir(parents=True, exist_ok=True)
    openapi_file.write_text(render_openapi_document(), encoding="utf-8")
    print(f"wrote {openapi_file}")


if __name__ == "__main__":
    main()
