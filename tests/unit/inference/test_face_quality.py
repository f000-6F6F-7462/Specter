import cv2
import numpy as np
import pytest

from specter.entities.geometry import BoundingBox
from specter.inference.face_quality import estimate_yaw_degrees, measure_face_quality
from specter.vision.detections import FaceDetection

ALIGNED_FACE_SIZE_PIXELS = 112


def build_face(nose_x: float) -> FaceDetection:
    return FaceDetection(
        confidence_ratio=0.9,
        bounding_box=BoundingBox(x=0, y=0, width=100, height=120),
        landmarks=((30.0, 40.0), (70.0, 40.0), (nose_x, 60.0), (35.0, 90.0), (65.0, 90.0)),
    )


def build_checkerboard() -> np.ndarray:
    tiles = np.indices((ALIGNED_FACE_SIZE_PIXELS, ALIGNED_FACE_SIZE_PIXELS)).sum(axis=0) // 8 % 2
    gray_image = (tiles * 255).astype(np.uint8)
    return np.asarray(cv2.cvtColor(gray_image, cv2.COLOR_GRAY2BGR), dtype=np.uint8)


@pytest.mark.parametrize(
    ("nose_x", "expected_yaw_degrees"), [(50.0, 0.0), (60.0, 30.0), (30.0, -90.0)]
)
def test_yaw_follows_how_far_the_nose_sits_off_the_eyes_middle(
    nose_x: float, expected_yaw_degrees: float
) -> None:
    assert estimate_yaw_degrees(build_face(nose_x)) == pytest.approx(expected_yaw_degrees)


def test_blurred_face_measures_blurrier_than_sharp_one() -> None:
    sharp_face = build_checkerboard()
    blurred_face = np.asarray(cv2.GaussianBlur(sharp_face, (15, 15), 5), dtype=np.uint8)

    sharp_report = measure_face_quality(build_face(50.0), sharp_face)
    blurred_report = measure_face_quality(build_face(50.0), blurred_face)

    assert sharp_report.blur_ratio < blurred_report.blur_ratio


def test_brightness_is_the_mean_intensity_when_face_is_measured() -> None:
    gray_face = np.full((ALIGNED_FACE_SIZE_PIXELS, ALIGNED_FACE_SIZE_PIXELS, 3), 51, np.uint8)

    report = measure_face_quality(build_face(50.0), gray_face)

    assert report.brightness_ratio == pytest.approx(0.2)
    assert report.face_height_pixels == 120
