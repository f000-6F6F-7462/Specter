import logging

import pytest

from specter.core.clock import Clock
from specter.core.di import Container, build_container
from specter.core.settings import Settings


def test_build_container_wires_settings_and_clock(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)
    container = build_container(Settings(env="test"))
    assert isinstance(container, Container)
    assert container.settings.env == "test"
    assert isinstance(container.clock, Clock)


def test_build_container_configures_root_logging(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    build_container(Settings(log={"level": "WARNING", "json": True}))  # type: ignore[arg-type]
    assert logging.getLogger().level == logging.WARNING
