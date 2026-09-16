"""Static deployment and hardware settings, validated when a process starts."""

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, override

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from specter.core.logging import LogFormat

DATABASE_FILE_NAME = "specter.sqlite3"
BYTES_PER_GIBIBYTE = 1024**3


class HardwareProfile(StrEnum):
    """Hardware the device runs on, which selects the detector defaults."""

    PC_CPU = "pc-cpu"
    PC_NVIDIA = "pc-nvidia"
    JETSON = "jetson"
    RASPBERRY_PI_HAILO = "raspberry-pi-hailo"


class DetectorBackend(StrEnum):
    """Runtime that executes the models."""

    ONNXRUNTIME = "onnxruntime"
    HAILO = "hailo"


DEFAULT_HARDWARE_PROFILE = HardwareProfile.PC_CPU

# ONNX Runtime tries providers in order, so CPU stays last as the fallback.
GPU_EXECUTION_PROVIDERS = [
    "TensorrtExecutionProvider",
    "CUDAExecutionProvider",
    "CPUExecutionProvider",
]

DETECTOR_PRESETS_BY_HARDWARE_PROFILE: dict[HardwareProfile, dict[str, Any]] = {
    HardwareProfile.PC_CPU: {
        "backend": DetectorBackend.ONNXRUNTIME,
        "onnxruntime_execution_providers": ["CPUExecutionProvider"],
        "max_batch_size": 4,
        "max_batch_delay_milliseconds": 5.0,
    },
    HardwareProfile.PC_NVIDIA: {
        "backend": DetectorBackend.ONNXRUNTIME,
        "onnxruntime_execution_providers": GPU_EXECUTION_PROVIDERS,
        "max_batch_size": 16,
        "max_batch_delay_milliseconds": 5.0,
    },
    HardwareProfile.JETSON: {
        "backend": DetectorBackend.ONNXRUNTIME,
        "onnxruntime_execution_providers": GPU_EXECUTION_PROVIDERS,
        "max_batch_size": 8,
        "max_batch_delay_milliseconds": 5.0,
    },
    HardwareProfile.RASPBERRY_PI_HAILO: {
        "backend": DetectorBackend.HAILO,
        "onnxruntime_execution_providers": ["CPUExecutionProvider"],
        "max_batch_size": 8,
        "max_batch_delay_milliseconds": 5.0,
    },
}


class _StrictModel(BaseModel):
    # Unknown keys are rejected so a typo in the settings file fails at startup.
    model_config = ConfigDict(extra="forbid")


class DeviceSettings(_StrictModel):
    """Identity and hardware of this device."""

    name: str = "specter"
    hardware_profile: HardwareProfile = DEFAULT_HARDWARE_PROFILE


class PathSettings(_StrictModel):
    """Directories on the device's disk."""

    data_directory: Path = Path("/var/lib/specter")
    models_directory: Path = Path("/opt/specter/models")

    @property
    def database_file(self) -> Path:
        """The SQLite database file inside the data directory."""
        return self.data_directory / DATABASE_FILE_NAME


class ServiceSettings(_StrictModel):
    """Addresses of the services that run next to Specter."""

    nats_url: str = "nats://localhost:4222"
    qdrant_url: str = "http://localhost:6333"
    go2rtc_url: str = "http://localhost:1984"
    # Camera processes read each camera from go2rtc's restream instead of the camera itself.
    go2rtc_rtsp_url: str = "rtsp://localhost:8554"


class DetectorSettings(_StrictModel):
    """How the detector runs models; unset fields come from the hardware profile preset."""

    backend: DetectorBackend
    onnxruntime_execution_providers: list[str]
    max_batch_size: int = Field(ge=1)
    max_batch_delay_milliseconds: float = Field(gt=0)


class MatchingSettings(_StrictModel):
    """How confirmed matches turn into alerts."""

    # The same target on the same camera raises at most one alert within this time.
    cooldown_seconds: float = Field(default=30.0, gt=0)


class EvidenceSettings(_StrictModel):
    """How long evidence snapshots are kept and how much disk they may use."""

    maximum_age_days: int = Field(default=30, ge=1)
    maximum_size_gibibytes: float = Field(default=5.0, gt=0)

    @property
    def maximum_size_bytes(self) -> int:
        """The disk quota in bytes."""
        return int(self.maximum_size_gibibytes * BYTES_PER_GIBIBYTE)


class SecuritySettings(_StrictModel):
    """Where secrets are kept on the device."""

    # Outside the data directory, so a copied database or backup never contains the key.
    credentials_key_file: Path = Path("/etc/specter/credentials.key")


class ApiSettings(_StrictModel):
    """Where the local HTTP API listens."""

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)


class LoggingSettings(_StrictModel):
    """Log level and output format."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    format: LogFormat = LogFormat.TEXT


class Settings(BaseSettings):
    """All static settings of a Specter process."""

    model_config = SettingsConfigDict(
        env_prefix="SPECTER_", env_nested_delimiter="__", extra="forbid"
    )

    device: DeviceSettings = Field(default_factory=DeviceSettings)
    paths: PathSettings = Field(default_factory=PathSettings)
    services: ServiceSettings = Field(default_factory=ServiceSettings)
    detector: DetectorSettings
    matching: MatchingSettings = Field(default_factory=MatchingSettings)
    evidence: EvidenceSettings = Field(default_factory=EvidenceSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)

    @model_validator(mode="before")
    @classmethod
    def apply_hardware_profile_preset(cls, values: Any) -> Any:
        """Fills detector settings that were not set explicitly from the hardware profile."""
        if not isinstance(values, dict):
            return values
        device = values.get("device")
        if isinstance(device, DeviceSettings):
            hardware_profile = device.hardware_profile
        elif isinstance(device, dict):
            hardware_profile = HardwareProfile(
                device.get("hardware_profile", DEFAULT_HARDWARE_PROFILE)
            )
        else:
            hardware_profile = DEFAULT_HARDWARE_PROFILE

        detector = values.get("detector")
        if isinstance(detector, DetectorSettings):
            return values
        explicit_detector_values = detector if isinstance(detector, dict) else {}
        preset = DETECTOR_PRESETS_BY_HARDWARE_PROFILE[hardware_profile]
        return {**values, "detector": {**preset, **explicit_detector_values}}

    @override
    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Gives environment variables precedence over the values read from the YAML file."""
        return (env_settings, init_settings)
