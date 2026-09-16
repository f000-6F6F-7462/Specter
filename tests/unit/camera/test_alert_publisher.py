import cv2
import numpy as np

from specter.camera.alert_publisher import encode_jpeg

MAXIMUM_MEAN_INTENSITY_ERROR = 3.0


def test_snapshot_decodes_to_the_same_image_when_encoded_as_jpeg() -> None:
    gradient = np.tile(np.linspace(0, 255, 321, dtype=np.uint8), (181, 1))
    image = np.dstack((gradient, np.flipud(gradient), np.full_like(gradient, 90)))

    decoded_image = cv2.imdecode(np.frombuffer(encode_jpeg(image), np.uint8), cv2.IMREAD_COLOR)

    assert decoded_image is not None
    assert decoded_image.shape == image.shape
    mean_error = np.abs(decoded_image.astype(np.int16) - image.astype(np.int16)).mean()
    assert mean_error < MAXIMUM_MEAN_INTENSITY_ERROR
