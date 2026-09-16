"""Finds faces and their five landmarks with SCRFD, InsightFace's face detector."""

from collections.abc import Sequence

import cv2
import numpy as np
from numpy.typing import NDArray

from specter.entities.geometry import BoundingBox
from specter.inference.backends import Tensor
from specter.vision.detections import FaceDetection
from specter.vision.frames import FrameImage

# SCRFD predicts at three scales, with two anchors at every location of each scale.
FEATURE_STRIDES = (8, 16, 32)
ANCHORS_PER_LOCATION = 2
LANDMARK_COUNT = 5
INPUT_MEAN_INTENSITY = 127.5
INPUT_SCALE = 1.0 / 128.0
DEFAULT_CONFIDENCE_THRESHOLD_RATIO = 0.5
DEFAULT_OVERLAP_THRESHOLD_RATIO = 0.4


class FaceDetector:
    """SCRFD's input preparation and output decoding, for one model input size.

    The model's outputs hold, for each scale in turn, the face scores, then the box distances,
    then the landmark distances.
    """

    def __init__(
        self,
        input_width: int,
        input_height: int,
        *,
        confidence_threshold_ratio: float = DEFAULT_CONFIDENCE_THRESHOLD_RATIO,
        overlap_threshold_ratio: float = DEFAULT_OVERLAP_THRESHOLD_RATIO,
    ) -> None:
        self._input_width = input_width
        self._input_height = input_height
        self._confidence_threshold_ratio = confidence_threshold_ratio
        self._overlap_threshold_ratio = overlap_threshold_ratio

    def prepare(self, image: FrameImage) -> tuple[Tensor, float]:
        """Returns the model input for a BGR image, and the scale from image to input pixels.

        The image is scaled to fit and placed at the top-left corner, as SCRFD was trained.
        """
        image_height, image_width = image.shape[:2]
        scale = min(self._input_width / image_width, self._input_height / image_height)
        scaled_width = max(round(image_width * scale), 1)
        scaled_height = max(round(image_height * scale), 1)
        canvas = np.zeros((self._input_height, self._input_width, 3), dtype=np.uint8)
        canvas[:scaled_height, :scaled_width] = cv2.resize(image, (scaled_width, scaled_height))
        rgb_image = canvas[..., ::-1].astype(np.float32)
        model_input = ((rgb_image - INPUT_MEAN_INTENSITY) * INPUT_SCALE).transpose(2, 0, 1)
        return np.ascontiguousarray(model_input[np.newaxis], dtype=np.float32), scale

    def decode(self, outputs: Sequence[Tensor], scale: float) -> list[FaceDetection]:
        """Returns the faces above the confidence threshold in image pixels, after NMS."""
        scale_count = len(FEATURE_STRIDES)
        all_scores: list[NDArray[np.float32]] = []
        all_boxes: list[NDArray[np.float32]] = []
        all_landmarks: list[NDArray[np.float32]] = []
        for scale_index, stride in enumerate(FEATURE_STRIDES):
            anchor_centers = self._build_anchor_centers(stride)
            scores = outputs[scale_index].reshape(-1)
            box_distances = outputs[scale_count + scale_index].reshape(-1, 4) * stride
            landmark_distances = (
                outputs[2 * scale_count + scale_index].reshape(-1, 2 * LANDMARK_COUNT) * stride
            )
            is_confident = scores >= self._confidence_threshold_ratio
            centers = anchor_centers[is_confident]
            distances = box_distances[is_confident]
            all_scores.append(scores[is_confident])
            all_boxes.append(
                np.concatenate((centers - distances[:, :2], centers + distances[:, 2:]), axis=1)
            )
            all_landmarks.append(
                np.repeat(centers, LANDMARK_COUNT, axis=0).reshape(-1, 2 * LANDMARK_COUNT)
                + landmark_distances[is_confident]
            )

        scores = np.concatenate(all_scores)
        if not len(scores):
            return []
        corner_boxes = np.concatenate(all_boxes) / scale
        landmarks = np.concatenate(all_landmarks) / scale
        size_boxes = np.concatenate(
            (corner_boxes[:, :2], corner_boxes[:, 2:] - corner_boxes[:, :2]), axis=1
        )
        kept_indices = cv2.dnn.NMSBoxes(
            size_boxes.tolist(),
            scores.tolist(),
            self._confidence_threshold_ratio,
            self._overlap_threshold_ratio,
        )
        return [
            FaceDetection(
                confidence_ratio=min(float(scores[index]), 1.0),
                bounding_box=BoundingBox(
                    x=round(float(size_boxes[index, 0])),
                    y=round(float(size_boxes[index, 1])),
                    width=max(round(float(size_boxes[index, 2])), 1),
                    height=max(round(float(size_boxes[index, 3])), 1),
                ),
                landmarks=tuple(
                    (float(point[0]), float(point[1]))
                    for point in landmarks[index].reshape(LANDMARK_COUNT, 2)
                ),
            )
            for index in np.asarray(kept_indices, dtype=np.int64).reshape(-1)
        ]

    def _build_anchor_centers(self, stride: int) -> NDArray[np.float32]:
        row_count = self._input_height // stride
        column_count = self._input_width // stride
        rows, columns = np.mgrid[:row_count, :column_count]
        centers = np.stack((columns, rows), axis=-1).reshape(-1, 2).astype(np.float32) * stride
        return np.repeat(centers, ANCHORS_PER_LOCATION, axis=0)
