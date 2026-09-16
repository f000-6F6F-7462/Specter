import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_health_reports_ok_when_api_is_connected_to_nats(connected_api_client: TestClient) -> None:
    response = connected_api_client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["is_nats_connected"] is True
