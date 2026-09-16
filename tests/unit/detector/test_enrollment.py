from pathlib import Path

import cv2
import numpy as np

from specter.detector.enrollment import build_enrollment_job_id, read_reference_image
from specter.entities.targets import EmbeddingModality


def test_reference_image_is_read_when_it_lies_inside_the_data_directory(tmp_path: Path) -> None:
    (tmp_path / "reference_images").mkdir()
    cv2.imwrite(str(tmp_path / "reference_images" / "jane.png"), np.zeros((8, 6, 3), np.uint8))

    image = read_reference_image(tmp_path, "reference_images/jane.png")

    assert image is not None
    assert image.shape == (8, 6, 3)


def test_reference_image_is_unreadable_when_missing_or_undecodable(tmp_path: Path) -> None:
    (tmp_path / "broken.jpg").write_bytes(b"not an image")

    assert read_reference_image(tmp_path, "missing.jpg") is None
    assert read_reference_image(tmp_path, "broken.jpg") is None


def test_reference_image_is_unreadable_when_path_leaves_the_data_directory(
    tmp_path: Path,
) -> None:
    data_directory = tmp_path / "data"
    data_directory.mkdir()
    cv2.imwrite(str(tmp_path / "outside.png"), np.zeros((4, 4, 3), np.uint8))

    assert read_reference_image(data_directory, "../outside.png") is None


def test_job_id_is_the_same_only_when_the_same_enrollment_is_queued_again() -> None:
    job_id = build_enrollment_job_id("image_jane", EmbeddingModality.FACE, "arcface_r50_onnx")

    assert job_id == build_enrollment_job_id(
        "image_jane", EmbeddingModality.FACE, "arcface_r50_onnx"
    )
    assert job_id != build_enrollment_job_id(
        "image_jane", EmbeddingModality.APPEARANCE, "arcface_r50_onnx"
    )
    assert job_id != build_enrollment_job_id("image_jane", EmbeddingModality.FACE, "arcface_next")
