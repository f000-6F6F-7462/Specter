"""Measures how usable a detected face is for recognition."""

import math

import cv2
import numpy as np

from specter.entities.targets import QualityReport
from specter.vision.detections import FaceDetection
from specter.vision.frames import FrameImage

MAXIMUM_PIXEL_INTENSITY = 255.0
# The Laplacian variance of an aligned 112-pixel face that is fully sharp; blurrier faces score
# proportionally lower.
SHARP_LAPLACIAN_VARIANCE = 300.0
LEFT_EYE_INDEX = 0
RIGHT_EYE_INDEX = 1
NOSE_INDEX = 2
MAXIMUM_YAW_DEGREES = 90.0


def measure_face_quality(face: FaceDetection, aligned_face: FrameImage) -> QualityReport:
    """Returns the measurements of a face, taken on its aligned crop where detail matters.

    Measuring the aligned crop compares faces of every size and angle on the same scale.
    """
    gray_face = cv2.cvtColor(aligned_face, cv2.COLOR_BGR2GRAY)
    laplacian_variance = float(cv2.Laplacian(gray_face, cv2.CV_64F).var())
    return QualityReport(
        detection_score_ratio=face.confidence_ratio,
        blur_ratio=1.0 - min(laplacian_variance / SHARP_LAPLACIAN_VARIANCE, 1.0),
        face_height_pixels=face.bounding_box.height,
        yaw_degrees=estimate_yaw_degrees(face),
        brightness_ratio=float(np.mean(gray_face)) / MAXIMUM_PIXEL_INTENSITY,
    )


def estimate_yaw_degrees(face: FaceDetection) -> float:
    """Estimates how far the face turns sideways, from how far the nose sits off the eyes' middle.

    A frontal face has its nose midway between the eyes, and a profile has it level with an eye.
    """
    left_eye_x = face.landmarks[LEFT_EYE_INDEX][0]
    right_eye_x = face.landmarks[RIGHT_EYE_INDEX][0]
    half_eye_distance = abs(right_eye_x - left_eye_x) / 2
    if half_eye_distance == 0:
        return MAXIMUM_YAW_DEGREES
    nose_offset_ratio = (face.landmarks[NOSE_INDEX][0] - (left_eye_x + right_eye_x) / 2) / (
        half_eye_distance
    )
    return math.degrees(math.asin(max(min(nose_offset_ratio, 1.0), -1.0)))
