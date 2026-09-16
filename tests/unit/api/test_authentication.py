import pytest
from fastapi.testclient import TestClient

from specter.api.main import create_app
from specter.config.settings import Settings

CAMERAS_PATH = "/owners/owner_alice/cameras"


@pytest.mark.parametrize("authorization", [None, "Bearer wrong-token", "Basic dGVzdA=="])
def test_request_is_rejected_when_it_lacks_the_api_token(
    api_settings: Settings, authorization: str | None
) -> None:
    headers = {} if authorization is None else {"Authorization": authorization}
    with TestClient(create_app(api_settings)) as client:
        response = client.get(CAMERAS_PATH, headers=headers)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_request_is_served_when_it_carries_the_api_token(api_client: TestClient) -> None:
    assert api_client.get(CAMERAS_PATH).status_code == 200


def test_health_check_needs_no_token(api_settings: Settings) -> None:
    with TestClient(create_app(api_settings)) as client:
        assert client.get("/health").status_code == 503
