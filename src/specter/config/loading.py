"""Loads static settings from a YAML file and environment variables."""

import os
from pathlib import Path
from typing import Any

import yaml

from specter.config.settings import Settings
from specter.core.errors import ConfigurationError

CONFIG_FILE_ENVIRONMENT_VARIABLE = "SPECTER_CONFIG_FILE"


def load_settings(config_file: Path | None = None) -> Settings:
    """Returns settings where environment variables override the file, which overrides defaults.

    Args:
        config_file: Falls back to ``SPECTER_CONFIG_FILE``; without either, only environment
            variables and defaults apply.

    Raises:
        ConfigurationError: The file cannot be read or does not contain a YAML mapping.
        pydantic.ValidationError: A value is unknown or invalid.
    """
    resolved_config_file = config_file or _read_config_file_from_environment()
    file_values = _read_yaml_mapping(resolved_config_file) if resolved_config_file else {}
    return Settings(**file_values)


def _read_config_file_from_environment() -> Path | None:
    configured_path = os.environ.get(CONFIG_FILE_ENVIRONMENT_VARIABLE)
    return Path(configured_path) if configured_path else None


def _read_yaml_mapping(config_file: Path) -> dict[str, Any]:
    try:
        content = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ConfigurationError(f"cannot read settings file {config_file}: {error}") from error
    if content is None:
        return {}
    if not isinstance(content, dict):
        raise ConfigurationError(f"settings file {config_file} must contain a YAML mapping")
    return content
