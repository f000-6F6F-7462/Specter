import hashlib
import io
import zipfile
from functools import partial
from pathlib import Path

import httpx
import pytest

from specter.config.settings import HardwareProfile
from specter.core.errors import ConfigurationError
from specter.inference.model_store import (
    ModelDownload,
    ModelFile,
    ModelFormat,
    ModelManifest,
    ModelRole,
    ModelSpecification,
    ModelStore,
    UltralyticsExport,
    load_model_manifest,
)

ARCHIVE_URL = "https://models.example/pack.zip"
ARCHIVE_MEMBER = "detector.onnx"
MODEL_CONTENT = b"onnx model bytes"


def build_manifest(model_file: ModelFile, *, is_exported: bool = False) -> ModelManifest:
    model = ModelSpecification(
        format=ModelFormat.ONNX,
        input_width=320,
        input_height=320,
        files=(model_file,),
        export=UltralyticsExport(kind="ultralytics", weights="yolo26n.pt") if is_exported else None,
        # One model plays every role here, including those that produce embeddings.
        embedding_size=512,
    )
    return ModelManifest(
        models={"test_model": model},
        profiles={profile: dict.fromkeys(ModelRole, "test_model") for profile in HardwareProfile},
    )


def build_downloaded_file(sha256: str) -> ModelFile:
    return ModelFile(
        path="pack/detector.onnx",
        sha256=sha256,
        download=ModelDownload(archive_url=ARCHIVE_URL, archive_member=ARCHIVE_MEMBER),
    )


def build_archive() -> bytes:
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr(ARCHIVE_MEMBER, MODEL_CONTENT)
    return archive_buffer.getvalue()


def respond_with_archive(request: httpx.Request, *, requested_urls: list[str]) -> httpx.Response:
    requested_urls.append(str(request.url))
    return httpx.Response(200, content=build_archive())


def build_http_client(requested_urls: list[str]) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(partial(respond_with_archive, requested_urls=requested_urls))
    )


def test_bundled_manifest_names_a_model_for_every_role_of_every_profile() -> None:
    manifest = load_model_manifest()

    for hardware_profile in HardwareProfile:
        assert set(manifest.profiles[hardware_profile]) == set(ModelRole)


def test_missing_published_file_is_downloaded_once_when_models_are_prepared(
    tmp_path: Path,
) -> None:
    requested_urls: list[str] = []
    manifest = build_manifest(build_downloaded_file(hashlib.sha256(MODEL_CONTENT).hexdigest()))
    model_store = ModelStore(tmp_path, manifest, build_http_client(requested_urls))

    model_store.prepare_profile_models(HardwareProfile.PC_CPU)
    model_store.prepare_profile_models(HardwareProfile.PC_CPU)

    assert (tmp_path / "pack" / "detector.onnx").read_bytes() == MODEL_CONTENT
    assert requested_urls == [ARCHIVE_URL]


def test_downloaded_file_is_rejected_when_its_checksum_does_not_match(tmp_path: Path) -> None:
    manifest = build_manifest(build_downloaded_file("0" * 64))
    model_store = ModelStore(tmp_path, manifest, build_http_client([]))

    with pytest.raises(ConfigurationError, match="fails its checksum"):
        model_store.prepare_profile_models(HardwareProfile.PC_CPU)

    assert not (tmp_path / "pack" / "detector.onnx").exists()


def test_preparing_fails_with_export_hint_when_exported_file_is_missing(tmp_path: Path) -> None:
    manifest = build_manifest(ModelFile(path="yolo26n_320.onnx"), is_exported=True)
    model_store = ModelStore(tmp_path, manifest, build_http_client([]))

    with pytest.raises(ConfigurationError, match="make models"):
        model_store.prepare_profile_models(HardwareProfile.PC_CPU)


def test_manifest_is_rejected_when_a_profile_names_an_undefined_model() -> None:
    model = ModelSpecification(
        format=ModelFormat.ONNX,
        input_width=320,
        input_height=320,
        files=(ModelFile(path="model.onnx"),),
        export=UltralyticsExport(kind="ultralytics", weights="yolo26n.pt"),
    )

    with pytest.raises(ValueError, match="not defined"):
        ModelManifest(
            models={"test_model": model},
            profiles={
                profile: dict.fromkeys(ModelRole, "other_model") for profile in HardwareProfile
            },
        )


def test_converted_model_keeps_the_embedding_version_of_its_source_model() -> None:
    manifest = load_model_manifest()

    assert manifest.embedding_version("arcface_mobilefacenet_ncnn") == "arcface_mobilefacenet_onnx"
    assert manifest.embedding_version("arcface_r50_onnx") == "arcface_r50_onnx"
