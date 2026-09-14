"""JPEG evidence codec.

Encodes snapshot / crop frames with OpenCV (``[ml]`` extra, imported lazily). Selected
when ``SPECTER_PIPELINE__EVIDENCE_FORMAT=jpeg``; the default gate uses the
dependency-free ``NumpyFrameCodec`` instead.
"""

import numpy as np

from specter.core.errors import DependencyFailure


class JpegFrameCodec:
    extension = ".jpg"
    content_type = "image/jpeg"

    def __init__(self, quality: int = 90) -> None:
        self._quality = quality

    def encode(self, image: np.ndarray) -> bytes:
        import cv2  # pylint: disable=import-outside-toplevel

        ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, self._quality])
        if not ok:
            raise DependencyFailure("cv2 failed to JPEG-encode an evidence frame")
        return bytes(buffer.tobytes())
