from pathlib import Path

from specter.api.openapi import render_openapi_document

OPENAPI_FILE = Path(__file__).resolve().parents[3] / "contracts" / "openapi.json"


def test_committed_openapi_document_matches_the_api_when_regenerated() -> None:
    assert OPENAPI_FILE.exists(), "contracts/openapi.json is missing: run make contracts"
    assert OPENAPI_FILE.read_text(encoding="utf-8") == render_openapi_document(), (
        "contracts/openapi.json is out of date: run make contracts"
    )
