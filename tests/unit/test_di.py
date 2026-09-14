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
    assert type(container.health).__name__ == "InMemoryHealthStore"
    assert container.lifecycle == ()  # nothing to batch — every adapter is a fake
    assert callable(container.frame_source_factory)


def test_redis_bus_selects_the_redis_health_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)
    container = build_container(make_settings(bus="redis"))
    assert type(container.bus).__name__ == "RedisStreamBus"
    assert type(container.health).__name__ == "RedisHealthStore"


def test_gstreamer_media_builds_a_lazy_factory(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    container = build_container(make_settings(media="gstreamer"))
    source = container.frame_source_factory(_STREAM)
    assert type(source).__name__ == "GStreamerFrameSource"


def test_pyav_media_builds_a_lazy_factory(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    container = build_container(make_settings(media="pyav"))
    source = container.frame_source_factory(_STREAM)
    assert type(source).__name__ == "PyAvFrameSource"


def test_webrtc_media_builds_a_lazy_factory(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    container = build_container(make_settings(media="webrtc"))
    webrtc_stream = StreamConfig(
        id="stream_webrtc",
        owner_id="o_di",
        name="cam",
        source=StreamSource(protocol=StreamProtocol.WEBRTC, url="https://cam/whep/1"),
    )
    source = container.frame_source_factory(webrtc_stream)
    assert type(source).__name__ == "WebRtcFrameSource"


def test_yolo_detector_builds_batched_without_importing_ultralytics(
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
    # Real detectors are wrapped in the shared, MicroBatcher-backed BatchedDetector so
    # every stream's calls coalesce into one forward pass; fakes stay direct-call.
    assert type(container.detector).__name__ == "BatchedDetector"
    assert len(container.lifecycle) == 1
    assert id(container.lifecycle[0]) == id(container.detector)


def test_bytetrack_tracker_selected_without_importing_the_trackers_package(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)
    container = build_container(make_settings(models={"tracker": {"impl": "bytetrack"}}))
    assert type(container.tracker).__name__ == "ByteTrackAdapter"


def test_unknown_tracker_impl_raises(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from specter.core.errors import ConfigurationError

    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigurationError, match="tracker"):
        build_container(make_settings(models={"tracker": {"impl": "bogus"}}))


def test_osnet_embedder_builds_batched_without_importing_torchreid(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)
    container = build_container(
        make_settings(
            models={
                "detector": {"impl": "fake"},
                "embedders": {"person": {"impl": "osnet", "weights": "osnet_x1_0.pth"}},
            }
        )
    )
    assert type(container.embedders["person"]).__name__ == "BatchedEmbedder"
    assert container.embedders["person"].modality == "person"
    assert len(container.lifecycle) == 1


def test_osnet_embedder_without_weights_raises(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from specter.core.errors import ConfigurationError

    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigurationError, match="weights"):
        build_container(
            make_settings(
                models={
                    "detector": {"impl": "fake"},
                    "embedders": {"person": {"impl": "osnet"}},
                }
            )
        )


def test_onnx_detector_builds_batched_without_importing_ultralytics(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)
    container = build_container(
        make_settings(
            models={
                "detector": {"impl": "onnx", "weights": "yolo11m.onnx"},
                "embedders": {"face": {"impl": "fake"}},
            }
        )
    )
    assert type(container.detector).__name__ == "BatchedDetector"
    assert len(container.lifecycle) == 1
    assert id(container.lifecycle[0]) == id(container.detector)


def test_onnx_detector_rejects_non_onnx_weights(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from specter.core.errors import ConfigurationError

    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigurationError, match=r"\.onnx"):
        build_container(
            make_settings(
                models={
                    "detector": {"impl": "onnx", "weights": "yolo11m.pt"},
                    "embedders": {"face": {"impl": "fake"}},
                }
            )
        )


def test_insightface_embedder_builds_batched_without_importing_the_model(
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
    assert type(container.embedders["face"]).__name__ == "BatchedEmbedder"
    assert container.embedders["face"].modality == "face"
    assert len(container.lifecycle) == 1
    assert id(container.lifecycle[0]) == id(container.embedders["face"])


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
