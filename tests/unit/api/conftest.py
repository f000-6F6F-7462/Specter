from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from specter.api.main import create_app
from specter.config.settings import PathSettings, SecuritySettings, ServiceSettings, Settings
from specter.storage.database import open_database
from specter.storage.migrate import apply_migrations

# Nothing listens on port 1, so every connection to NATS, Qdrant or go2rtc is refused at once.
UNREACHABLE_NATS_URL = "nats://127.0.0.1:1"
UNREACHABLE_HTTP_URL = "http://127.0.0.1:1"
API_TOKEN = "test-api-token"


@pytest.fixture
def api_settings(tmp_path: Path) -> Settings:
    (tmp_path / "secrets").mkdir()
    api_token_file = tmp_path / "secrets" / "api.token"
    api_token_file.write_text(API_TOKEN, encoding="utf-8")
    settings = Settings(
        paths=PathSettings(data_directory=tmp_path / "data"),
        services=ServiceSettings(
            nats_url=UNREACHABLE_NATS_URL,
            qdrant_url=UNREACHABLE_HTTP_URL,
            go2rtc_url=UNREACHABLE_HTTP_URL,
        ),
        security=SecuritySettings(
            credentials_key_file=tmp_path / "secrets" / "credentials.key",
            api_token_file=api_token_file,
        ),
    )
    database = open_database(settings.paths.database_file)
    apply_migrations(database)
    database.close()
    return settings


@pytest.fixture
def api_client(api_settings: Settings) -> Iterator[TestClient]:
    with TestClient(
        create_app(api_settings), headers={"Authorization": f"Bearer {API_TOKEN}"}
    ) as client:
        yield client
