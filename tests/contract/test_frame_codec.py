"""FrameCodec contract — holds for NumpyFrameCodec and JpegFrameCodec alike."""

import numpy as np

from specter.application.ports import FrameCodec


def _image() -> np.ndarray:
    return np.random.default_rng(1).integers(0, 256, size=(48, 64, 3), dtype=np.uint8)


def test_encode_produces_non_empty_bytes(frame_codec: FrameCodec) -> None:
    raw = frame_codec.encode(_image())
    assert isinstance(raw, bytes)
    assert len(raw) > 0


def test_encode_is_deterministic(frame_codec: FrameCodec) -> None:
    image = _image()
    assert frame_codec.encode(image) == frame_codec.encode(image)


def test_metadata_is_well_formed(frame_codec: FrameCodec) -> None:
    assert frame_codec.extension.startswith(".")
    assert "/" in frame_codec.content_type
