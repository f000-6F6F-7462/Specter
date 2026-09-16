import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from specter.api.main import create_app
from specter.config.settings import PathSettings, SecuritySettings, ServiceSettings, Settings
from specter.storage.database import open_database
from specter.storage.migrate import apply_migrations

API_TOKEN = "integration-api-token"
CONNECTION_TIMEOUT_SECONDS = 5.0
POLL_INTERVAL_SECONDS = 0.05
TEST_QDRANT_URL_ENVIRONMENT_VARIABLE = "SPECTER_TEST_QDRANT_URL"
DEFAULT_TEST_QDRANT_URL = "http://127.0.0.1:6333"


@pytest.fixture
def connected_api_settings(tmp_path: Path, nats_server_url: str, go2rtc_api_url: str) -> Settings:
    (tmp_path / "secrets").mkdir()
    api_token_file = tmp_path / "secrets" / "api.token"
    api_token_file.write_text(API_TOKEN, encoding="utf-8")
    settings = Settings(
        paths=PathSettings(data_directory=tmp_path / "data"),
        services=ServiceSettings(
            nats_url=nats_server_url,
            qdrant_url=os.environ.get(
                TEST_QDRANT_URL_ENVIRONMENT_VARIABLE, DEFAULT_TEST_QDRANT_URL
            ),
            go2rtc_url=go2rtc_api_url,
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
def connected_api_client(connected_api_settings: Settings) -> Iterator[TestClient]:
    with TestClient(
        create_app(connected_api_settings), headers={"Authorization": f"Bearer {API_TOKEN}"}
    ) as client:
        # The API connects to NATS in the background, so tests wait until it has.
        deadline = time.monotonic() + CONNECTION_TIMEOUT_SECONDS
        while client.get("/health").status_code != 200:
            if time.monotonic() > deadline:
                pytest.fail("the API did not connect to NATS in time")
            time.sleep(POLL_INTERVAL_SECONDS)
        yield client
