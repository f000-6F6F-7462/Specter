import pytest
from pydantic import ValidationError

from specter.agent.config import AgentSettings
from specter.config.settings import Settings


def test_agent_settings_defaults() -> None:
    settings = AgentSettings()
    assert settings.mode == "local"
    assert settings.backend_url == "http://localhost:12113"
    assert settings.agent_token == ""
    assert settings.owner_id == "facealert"
    assert settings.specter_api_url == "http://127.0.0.1:8000"
    assert settings.heartbeat_interval_seconds == 30.0
    assert settings.sync_interval_seconds == 30.0
    assert settings.sync_on_connect is True
    assert len(settings.ice_servers) == 1
    assert settings.ice_servers[0]["urls"] == "stun:stun.cloudflare.com:3478"


def test_agent_settings_in_full_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPECTER_AGENT__MODE", "cloud")
    monkeypatch.setenv("SPECTER_AGENT__BACKEND_URL", "https://api.facealert.io")
    monkeypatch.setenv("SPECTER_AGENT__AGENT_TOKEN", "secret-agent-token")

    settings = Settings()
    assert settings.agent.mode == "cloud"
    assert settings.agent.backend_url == "https://api.facealert.io"
    assert settings.agent.agent_token == "secret-agent-token"


def test_agent_settings_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        AgentSettings.model_validate({"unknown_field": 123})
