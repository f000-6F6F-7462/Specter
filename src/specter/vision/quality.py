"""Quality gate for face crops, shared by enrollment and real-time matching."""

import math
from dataclasses import dataclass

from specter.entities.targets import QualityReport, RejectionReason


@dataclass(frozen=True, slots=True)
class QualityThresholds:
    """Limits a face crop must stay within to be used."""

    minimum_detection_score_ratio: float = 0.5
    maximum_blur_ratio: float = 0.6
    minimum_face_height_pixels: int = 40
    maximum_absolute_yaw_degrees: float = 45.0
    minimum_brightness_ratio: float = 0.2
    maximum_brightness_ratio: float = 0.9


ENROLLMENT_QUALITY_THRESHOLDS = QualityThresholds(maximum_blur_ratio=0.85)
# Live frames are smaller and less posed than enrollment photos, so the gate is looser.
RUNTIME_QUALITY_THRESHOLDS = QualityThresholds(
    minimum_detection_score_ratio=0.4,
    maximum_blur_ratio=0.75,
    minimum_face_height_pixels=32,
    maximum_absolute_yaw_degrees=55.0,
)


# A face this tall already carries all the detail that the 112-pixel embedding model can use.
FULL_DETAIL_FACE_HEIGHT_PIXELS = 112


@dataclass(frozen=True, slots=True)
class QualityVerdict:
    """Whether a face crop passed the gate, and every reason it did not."""

    has_passed: bool
    rejection_reasons: tuple[RejectionReason, ...]


def assess_quality(report: QualityReport, thresholds: QualityThresholds) -> QualityVerdict:
    """Returns the verdict of the quality gate for one face crop."""
    rejection_reasons: list[RejectionReason] = []
    if report.detection_score_ratio < thresholds.minimum_detection_score_ratio:
        rejection_reasons.append(RejectionReason.LOW_DETECTION_SCORE)
    if report.blur_ratio > thresholds.maximum_blur_ratio:
        rejection_reasons.append(RejectionReason.TOO_BLURRY)
    if report.face_height_pixels < thresholds.minimum_face_height_pixels:
        rejection_reasons.append(RejectionReason.TOO_SMALL)
    if abs(report.yaw_degrees) > thresholds.maximum_absolute_yaw_degrees:
        rejection_reasons.append(RejectionReason.EXTREME_POSE)
    if report.brightness_ratio < thresholds.minimum_brightness_ratio:
        rejection_reasons.append(RejectionReason.TOO_DARK)
    if report.brightness_ratio > thresholds.maximum_brightness_ratio:
        rejection_reasons.append(RejectionReason.TOO_BRIGHT)
    return QualityVerdict(
        has_passed=not rejection_reasons, rejection_reasons=tuple(rejection_reasons)
    )


def score_quality(report: QualityReport) -> float:
    """Returns how useful the face crop is for recognition, from 0 to 1, to rank crops of a face.

    Sharpness, a frontal pose, detail and the detector's confidence all raise the score, and any one
    of them near its worst drags the whole score down.
    """
    size_ratio = min(report.face_height_pixels / FULL_DETAIL_FACE_HEIGHT_PIXELS, 1.0)
    pose_ratio = max(math.cos(math.radians(report.yaw_degrees)), 0.0)
    return report.detection_score_ratio * (1.0 - report.blur_ratio) * pose_ratio * size_ratio
