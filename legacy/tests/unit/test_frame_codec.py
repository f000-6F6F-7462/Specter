"""``NumpyFrameCodec`` — the dependency-free evidence codec."""

import io

import numpy as np

from specter.infrastructure.media.codec import NumpyFrameCodec


def test_encode_round_trips_exactly_through_numpy_load() -> None:
    image = np.random.default_rng(7).integers(0, 256, size=(12, 16, 3), dtype=np.uint8)
    codec = NumpyFrameCodec()

    raw = codec.encode(image)
    restored = np.load(io.BytesIO(raw))

    assert np.array_equal(restored, image)


def test_advertises_its_extension_and_content_type() -> None:
    codec = NumpyFrameCodec()
    assert codec.extension == ".npy"
    assert codec.content_type == "application/x-npy"
