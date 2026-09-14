"""Dependency-free evidence codec.

``NumpyFrameCodec`` serialises an evidence frame with ``numpy.save`` — lossless, no
image library needed, and the bytes load straight back with ``numpy.load``. It is the
codec used by the default test/local gate; production selects ``JpegFrameCodec`` from
the ``[ml]`` extra instead.
"""

import io

import numpy as np


class NumpyFrameCodec:
    extension = ".npy"
    content_type = "application/x-npy"

    def encode(self, image: np.ndarray) -> bytes:
        buf = io.BytesIO()
        np.save(buf, image)
        return buf.getvalue()
