import pytest
from fastapi.testclient import TestClient

from specter.api.main import create_app
from specter.config.settings import ServiceSettings, Settings

pytestmark = pytest.mark.integration


def test_health_reports_ok_when_api_is_connected_to_nats(nats_server_url: str) -> None:
    settings = Settings(services=ServiceSettings(nats_url=nats_server_url))

    with TestClient(create_app(settings)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["is_nats_connected"] is True
