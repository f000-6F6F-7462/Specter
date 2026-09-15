import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from specter.config.loading import CONFIG_FILE_ENVIRONMENT_VARIABLE, load_settings
from specter.config.settings import DetectorBackend, HardwareProfile
from specter.core.errors import ConfigurationError


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable_name in list(os.environ):
        if variable_name.startswith("SPECTER_"):
            monkeypatch.delenv(variable_name)


def write_config_file(directory: Path, content: str) -> Path:
    config_file = directory / "specter.yaml"
    config_file.write_text(content, encoding="utf-8")
    return config_file


def test_defaults_apply_when_no_config_file_is_given() -> None:
    settings = load_settings()

    assert settings.device.hardware_profile is HardwareProfile.PC_CPU
    assert settings.detector.backend is DetectorBackend.ONNXRUNTIME


def test_preset_fills_detector_settings_when_detector_is_not_configured(tmp_path: Path) -> None:
    config_file = write_config_file(tmp_path, "device:\n  hardware_profile: raspberry-pi-hailo\n")

    settings = load_settings(config_file)

    assert settings.detector.backend is DetectorBackend.HAILO
    assert settings.detector.max_batch_size == 8


def test_explicit_detector_field_overrides_preset_when_set_in_file(tmp_path: Path) -> None:
    config_file = write_config_file(
        tmp_path, "device:\n  hardware_profile: pc-nvidia\ndetector:\n  max_batch_size: 2\n"
    )

    settings = load_settings(config_file)

    assert settings.detector.max_batch_size == 2
    assert "CUDAExecutionProvider" in settings.detector.onnxruntime_execution_providers


def test_environment_variable_overrides_file_value_when_both_are_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = write_config_file(tmp_path, "api:\n  port: 9000\n  host: 0.0.0.0\n")
    monkeypatch.setenv("SPECTER_API__PORT", "9100")

    settings = load_settings(config_file)

    assert settings.api.port == 9100
    assert settings.api.host == "0.0.0.0"


def test_hardware_profile_from_environment_selects_preset_when_file_has_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SPECTER_DEVICE__HARDWARE_PROFILE", "raspberry-pi-hailo")

    settings = load_settings()

    assert settings.detector.backend is DetectorBackend.HAILO


def test_config_file_from_environment_is_used_when_no_path_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = write_config_file(tmp_path, "device:\n  name: front-door\n")
    monkeypatch.setenv(CONFIG_FILE_ENVIRONMENT_VARIABLE, str(config_file))

    settings = load_settings()

    assert settings.device.name == "front-door"


def test_loading_fails_when_config_file_does_not_exist(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        load_settings(tmp_path / "missing.yaml")


def test_loading_fails_when_config_file_is_not_a_mapping(tmp_path: Path) -> None:
    config_file = write_config_file(tmp_path, "- device\n- api\n")

    with pytest.raises(ConfigurationError):
        load_settings(config_file)


def test_loading_fails_when_config_file_contains_an_unknown_key(tmp_path: Path) -> None:
    config_file = write_config_file(tmp_path, "api:\n  prot: 9000\n")

    with pytest.raises(ValidationError):
        load_settings(config_file)


def test_loading_fails_when_hardware_profile_is_unknown(tmp_path: Path) -> None:
    config_file = write_config_file(tmp_path, "device:\n  hardware_profile: toaster\n")

    with pytest.raises(ValidationError):
        load_settings(config_file)
