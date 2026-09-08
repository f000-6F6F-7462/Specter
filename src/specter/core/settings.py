"""Typed configuration.

Layering (highest precedence first): constructor args, ``SPECTER_*`` environment
variables, ``.env``, ``settings.toml``, field defaults. Nested groups use a double
underscore, e.g. ``SPECTER_REDIS__URL``.
"""

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


class LogSettings(BaseModel):
    level: str = "INFO"
    # env / toml key stays "json"; the field is renamed to avoid shadowing BaseModel.json.
    json_output: bool = Field(default=False, validation_alias="json")


class RedisSettings(BaseModel):
    url: str = "redis://localhost:6379/0"
    stream_maxlen: int = 100_000
    consumer_group: str = "specter"


class DatabaseSettings(BaseModel):
    url: str = "postgresql+asyncpg://specter:specter@localhost:5432/specter"


class QdrantSettings(BaseModel):
    url: str = "http://localhost:6333"


class S3Settings(BaseModel):
    endpoint_url: str = "http://localhost:9000"
    access_key: str = "specter"
    secret_key: SecretStr = SecretStr("specter123")
    bucket: str = "specter"
    region: str = "us-east-1"
    secure: bool = False


class SecuritySettings(BaseModel):
    api_key: SecretStr = SecretStr("dev-only-change-me")
    secret_key: SecretStr = SecretStr("dev-only-fernet-key-change-me")


class DetectorSettings(BaseModel):
    impl: str = "yolo"
    weights: str = "yolo11m.pt"
    device: str = "mps"
    max_batch: int = Field(default=16, ge=1)
    max_delay_ms: float = Field(default=8.0, gt=0)


class EmbedderSettings(BaseModel):
    impl: str
    name: str | None = None
    weights: str | None = None
    max_batch: int = Field(default=16, ge=1)
    max_delay_ms: float = Field(default=8.0, gt=0)


class ModelSettings(BaseModel):
    detector: DetectorSettings = DetectorSettings()
    embedders: dict[str, EmbedderSettings] = Field(
        default_factory=lambda: {"face": EmbedderSettings(impl="insightface", name="buffalo_l")}
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SPECTER_",
        env_nested_delimiter="__",
        env_file=".env",
        toml_file="settings.toml",
        extra="ignore",
    )

    env: str = "local"
    bus: str = "redis"  # redis | memory
    blob: str = "minio"  # minio | memory
    vectors: str = "qdrant"  # qdrant | memory
    inference: str = "insightface"  # insightface | fake

    log: LogSettings = LogSettings()
    redis: RedisSettings = RedisSettings()
    database: DatabaseSettings = DatabaseSettings()
    qdrant: QdrantSettings = QdrantSettings()
    s3: S3Settings = S3Settings()
    security: SecuritySettings = SecuritySettings()
    models: ModelSettings = ModelSettings()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )
