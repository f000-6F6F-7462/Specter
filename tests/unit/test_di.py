import logging

import pytest

from specter.core.clock import Clock
from specter.core.di import Container, build_container
from tests.support import make_settings


def test_build_container_wires_the_memory_adapters(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)
    container = build_container(make_settings(env="test"))

    assert isinstance(container, Container)
    assert container.settings.env == "test"
    assert isinstance(container.clock, Clock)
    assert type(container.bus).__name__ == "MemoryBus"
    assert type(container.vectors).__name__ == "InMemoryVectorIndex"
    assert type(container.faces).__name__ == "FakeFaceEmbeddingService"
    assert type(container.blob).__name__ == "MemoryBlobStore"


def test_build_container_configures_root_logging(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    build_container(make_settings(log={"level": "WARNING", "json": True}))
    assert logging.getLogger().level == logging.WARNING
