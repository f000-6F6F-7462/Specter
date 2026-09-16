from fastapi.testclient import TestClient

from specter.api.main import create_app
from specter.config.settings import Settings


def test_health_reports_degraded_when_nats_is_unreachable(api_settings: Settings) -> None:
    with TestClient(create_app(api_settings)) as client:
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["is_nats_connected"] is False
