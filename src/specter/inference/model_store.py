"""Model files on the device: which models each hardware profile runs, and fetching them."""

import hashlib
import logging
import tempfile
import zipfile
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from specter.config.settings import HardwareProfile
from specter.core.errors import ConfigurationError

logger = logging.getLogger(__name__)

MANIFEST_FILE = Path(__file__).with_name("model_manifest.yaml")
DOWNLOAD_TIMEOUT_SECONDS = 300.0
HASH_CHUNK_SIZE_BYTES = 1024 * 1024
PARTIAL_FILE_SUFFIX = ".partial"


class ModelRole(StrEnum):
    """What a model is used for."""

    OBJECT_DETECTION = "object_detection"
    FACE_DETECTION = "face_detection"
    FACE_RECOGNITION = "face_recognition"
    APPEARANCE = "appearance"


class ModelFormat(StrEnum):
    """The file format a model is stored in, which selects the runtime that runs it."""

    ONNX = "onnx"
    NCNN = "ncnn"


class _ManifestPart(BaseModel):
    # Unknown keys are rejected so a typo in the manifest fails when the detector starts.
    model_config = ConfigDict(frozen=True, extra="forbid")


class ModelDownload(_ManifestPart):
    """Where a published file comes from: a member of a zip archive."""

    archive_url: str
    archive_member: str


class ModelFile(_ManifestPart):
    """One file of a model, relative to the models directory."""

    path: str
    sha256: str | None = None
    download: ModelDownload | None = None


class UltralyticsExport(_ManifestPart):
    """Exported from Ultralytics weights."""

    kind: Literal["ultralytics"]
    weights: str


class OsnetExport(_ManifestPart):
    """Exported from torchreid's OSNet definition and published weights."""

    kind: Literal["osnet"]
    variant: str
    weights_url: str
    definition_url: str


class PnnxExport(_ManifestPart):
    """Converted by pnnx from another model of the manifest."""

    kind: Literal["pnnx"]
    source_model: str


type ModelExport = Annotated[
    UltralyticsExport | OsnetExport | PnnxExport, Field(discriminator="kind")
]


class ModelSpecification(_ManifestPart):
    """A model's format, input size and files."""

    format: ModelFormat
    input_width: int = Field(gt=0)
    input_height: int = Field(gt=0)
    files: tuple[ModelFile, ...] = Field(min_length=1)
    export: ModelExport | None = None


class ModelManifest(_ManifestPart):
    """Every model Specter can run, and which of them each hardware profile uses."""

    models: dict[str, ModelSpecification]
    profiles: dict[HardwareProfile, dict[ModelRole, str]]

    @model_validator(mode="after")
    def require_consistent_references(self) -> Self:
        """Rejects a profile or export that names a missing model, and a file with no source."""
        for hardware_profile in HardwareProfile:
            if set(self.profiles.get(hardware_profile, {})) != set(ModelRole):
                raise ValueError(f"profile {hardware_profile} must name a model for every role")
        for model_ids_by_role in self.profiles.values():
            for model_id in model_ids_by_role.values():
                if model_id not in self.models:
                    raise ValueError(f"a profile uses model {model_id}, which is not defined")
        for model_id, model in self.models.items():
            if (
                isinstance(model.export, PnnxExport)
                and model.export.source_model not in self.models
            ):
                raise ValueError(
                    f"model {model_id} converts missing model {model.export.source_model}"
                )
            if model.export is None and any(file.download is None for file in model.files):
                raise ValueError(
                    f"model {model_id} has a file that is neither downloaded nor exported"
                )
        return self


def load_model_manifest(manifest_file: Path = MANIFEST_FILE) -> ModelManifest:
    """Returns the model manifest.

    Raises:
        ConfigurationError: The manifest cannot be read or is invalid.
    """
    try:
        return ModelManifest.model_validate(
            yaml.safe_load(manifest_file.read_text(encoding="utf-8"))
        )
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise ConfigurationError(f"invalid model manifest {manifest_file}: {error}") from error


class ModelStore:
    """The models directory, where published model files are downloaded when they are missing.

    Files produced by ``make models`` cannot be downloaded, so a missing one fails with a message
    that says how to produce it.
    """

    def __init__(
        self,
        models_directory: Path,
        manifest: ModelManifest,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._models_directory = models_directory
        self._manifest = manifest
        self._http_client = http_client or httpx.Client(
            follow_redirects=True, timeout=DOWNLOAD_TIMEOUT_SECONDS
        )

    def prepare_profile_models(
        self, hardware_profile: HardwareProfile
    ) -> dict[ModelRole, ModelSpecification]:
        """Makes sure every model of the profile is on disk and returns the models by role.

        Blocks while downloading, so asynchronous code runs it on a worker thread.

        Raises:
            ConfigurationError: A file fails to download, fails its checksum, or must be exported.
        """
        models_by_role = {
            role: self._manifest.models[model_id]
            for role, model_id in self._manifest.profiles[hardware_profile].items()
        }
        with tempfile.TemporaryDirectory(dir=self._ensure_models_directory()) as work_directory:
            downloaded_archives: dict[str, Path] = {}
            for model in models_by_role.values():
                for model_file in model.files:
                    self._prepare_file(model_file, Path(work_directory), downloaded_archives)
        return models_by_role

    def resolve_file(self, model_file: ModelFile) -> Path:
        """Returns where the file is stored."""
        return self._models_directory / model_file.path

    def _ensure_models_directory(self) -> Path:
        try:
            self._models_directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise ConfigurationError(
                f"cannot create the models directory {self._models_directory}: {error}"
            ) from error
        return self._models_directory

    def _prepare_file(
        self,
        model_file: ModelFile,
        work_directory: Path,
        downloaded_archives: dict[str, Path],
    ) -> None:
        stored_file = self.resolve_file(model_file)
        if stored_file.exists() and self._matches_checksum(stored_file, model_file.sha256):
            return
        if model_file.download is None:
            raise ConfigurationError(
                f"model file {stored_file} is missing or damaged; run `make models` and copy the "
                "models directory to the device"
            )
        archive_url = model_file.download.archive_url
        if archive_url not in downloaded_archives:
            downloaded_archives[archive_url] = self._download_archive(archive_url, work_directory)
        self._extract_member(
            model_file.download,
            model_file.sha256,
            downloaded_archives[archive_url],
            stored_file,
        )

    def _download_archive(self, archive_url: str, work_directory: Path) -> Path:
        logger.info("downloading model archive", extra={"archive_url": archive_url})
        archive_file = work_directory / f"{len(list(work_directory.iterdir()))}.zip"
        try:
            with self._http_client.stream("GET", archive_url) as response:
                response.raise_for_status()
                with archive_file.open("wb") as archive_stream:
                    for chunk in response.iter_bytes(HASH_CHUNK_SIZE_BYTES):
                        archive_stream.write(chunk)
        except httpx.HTTPError as error:
            raise ConfigurationError(
                f"cannot download model archive {archive_url}: {error}; without internet access, "
                "copy the models directory from a device that has it"
            ) from error
        return archive_file

    def _extract_member(
        self,
        download: ModelDownload,
        expected_sha256: str | None,
        archive_file: Path,
        stored_file: Path,
    ) -> None:
        stored_file.parent.mkdir(parents=True, exist_ok=True)
        partial_file = stored_file.with_name(stored_file.name + PARTIAL_FILE_SUFFIX)
        try:
            with (
                zipfile.ZipFile(archive_file) as archive,
                partial_file.open("wb") as partial_stream,
            ):
                partial_stream.write(archive.read(download.archive_member))
        except (zipfile.BadZipFile, KeyError) as error:
            partial_file.unlink(missing_ok=True)
            raise ConfigurationError(
                f"model archive {download.archive_url} has no usable "
                f"{download.archive_member}: {error}"
            ) from error
        if not self._matches_checksum(partial_file, expected_sha256):
            partial_file.unlink()
            raise ConfigurationError(f"downloaded model file {stored_file} fails its checksum")
        # Renaming only a verified file keeps a broken download from ever looking complete.
        partial_file.replace(stored_file)

    @staticmethod
    def _matches_checksum(file: Path, expected_sha256: str | None) -> bool:
        if expected_sha256 is None:
            return True
        digest = hashlib.sha256()
        with file.open("rb") as stream:
            while chunk := stream.read(HASH_CHUNK_SIZE_BYTES):
                digest.update(chunk)
        return digest.hexdigest() == expected_sha256
