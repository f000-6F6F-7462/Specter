import logging

import pytest

from specter.core.clock import Clock
from specter.core.di import Container, build_container
from specter.domain.streams import StreamConfig, StreamProtocol, StreamSource
from tests.support import make_settings

_STREAM = StreamConfig(
    id="stream_di",
    owner_id="o_di",
    name="cam",
    source=StreamSource(protocol=StreamProtocol.RTSP, url="rtsp://cam/1"),
)


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
    assert type(container.detector).__name__ == "FakeDetector"
    assert type(container.tracker).__name__ == "IouTracker"
    assert list(container.embedders) == ["face"]
    assert type(container.embedders["face"]).__name__ == "FakeEmbedder"
    assert type(container.codec).__name__ == "NumpyFrameCodec"
    assert callable(container.frame_source_factory)


def test_gstreamer_media_builds_a_lazy_factory(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    container = build_container(make_settings(media="gstreamer"))
    source = container.frame_source_factory(_STREAM)
    assert type(source).__name__ == "GStreamerFrameSource"


def test_yolo_detector_builds_without_importing_ultralytics(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)
    container = build_container(
        make_settings(
            models={
                "detector": {"impl": "yolo"},
                "embedders": {"face": {"impl": "fake"}},
            }
        )
    )
    assert type(container.detector).__name__ == "YoloDetector"


def test_insightface_embedder_builds_without_importing_the_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)
    container = build_container(
        make_settings(
            models={
                "detector": {"impl": "fake"},
                "embedders": {"face": {"impl": "insightface", "name": "buffalo_l"}},
            }
        )
    )
    assert type(container.embedders["face"]).__name__ == "FaceEmbedder"


def test_jpeg_evidence_codec_is_selectable(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    container = build_container(make_settings(pipeline={"evidence_format": "jpeg"}))
    assert type(container.codec).__name__ == "JpegFrameCodec"


def test_unknown_evidence_format_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from specter.core.errors import ConfigurationError

    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigurationError):
        build_container(make_settings(pipeline={"evidence_format": "tiff"}))


def test_build_container_configures_root_logging(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    build_container(make_settings(log={"level": "WARNING", "json": True}))
    assert logging.getLogger().level == logging.WARNING
