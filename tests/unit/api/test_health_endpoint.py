from fastapi.testclient import TestClient

from specter.api.main import create_app
from specter.config.settings import ServiceSettings, Settings

# Nothing listens on port 1, so every connection attempt is refused at once.
UNREACHABLE_NATS_URL = "nats://127.0.0.1:1"


def test_health_reports_degraded_when_nats_is_unreachable() -> None:
    settings = Settings(services=ServiceSettings(nats_url=UNREACHABLE_NATS_URL))

    with TestClient(create_app(settings)) as client:
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["is_nats_connected"] is False
