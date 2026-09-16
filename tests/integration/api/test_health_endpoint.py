import time

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from specter.api.main import create_app
from specter.config.settings import ServiceSettings, Settings

pytestmark = pytest.mark.integration

CONNECTION_TIMEOUT_SECONDS = 5.0
POLL_INTERVAL_SECONDS = 0.05


def test_health_reports_ok_when_api_is_connected_to_nats(nats_server_url: str) -> None:
    settings = Settings(services=ServiceSettings(nats_url=nats_server_url))

    with TestClient(create_app(settings)) as client:
        response = wait_for_successful_health_response(client)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["is_nats_connected"] is True


def wait_for_successful_health_response(client: TestClient) -> Response:
    # The API connects to NATS in the background, so the first requests may come before it.
    deadline = time.monotonic() + CONNECTION_TIMEOUT_SECONDS
    response: Response = client.get("/health")
    while response.status_code != 200 and time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL_SECONDS)
        response = client.get("/health")
    return response
