"""Helpers shared by the api and integration suites."""

from typing import Any

from specter.core.settings import Settings


def test_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "database": {"url": "sqlite+aiosqlite:///:memory:"},
        "security": {"api_key": "test-key"},
        "log": {"level": "WARNING"},
    }
    base.update(overrides)
    return Settings(**base)
