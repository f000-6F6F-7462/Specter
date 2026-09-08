"""Helpers shared by the api and integration suites."""

from typing import Any

from specter.core.settings import Settings


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "database": {"url": "sqlite+aiosqlite:///:memory:"},
        "security": {"api_key": "test-key"},
        "log": {"level": "WARNING"},
        # In-process adapters — no Redis / MinIO / Qdrant / ONNX models needed.
        "bus": "memory",
        "blob": "memory",
        "vectors": "memory",
        "inference": "fake",
    }
    base.update(overrides)
    return Settings(**base)
