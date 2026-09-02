"""Image-quality gate, shared by the enrollment and runtime pipelines.

``blur`` is a normalised blurriness score in ``[0, 1]`` (higher = blurrier).
``yaw_deg`` is absolute head yaw. Runtime thresholds are looser than enrollment.
"""

from dataclasses import dataclass
from enum import StrEnum


class RejectionReason(StrEnum):
    NO_DETECTION = "no_detection"
    MULTIPLE_FACES = "multiple_faces"
    TOO_SMALL = "too_small"
    TOO_BLURRY = "too_blurry"
    EXTREME_POSE = "extreme_pose"
    LOW_LIGHT = "low_light"
    DUPLICATE = "duplicate_of_existing"


@dataclass(frozen=True, slots=True)
class QualityReport:
    score: float
    blur: float
    face_px: int
    yaw_deg: float
    brightness: float


@dataclass(frozen=True, slots=True)
class QualityThresholds:
    min_score: float = 0.5
    max_blur: float = 0.6
    min_face_px: int = 40
    max_yaw_deg: float = 45.0
    min_brightness: float = 0.2
    max_brightness: float = 0.9


ENROLLMENT_THRESHOLDS = QualityThresholds()
RUNTIME_THRESHOLDS = QualityThresholds(
    min_score=0.4, max_blur=0.75, min_face_px=32, max_yaw_deg=55.0
)


@dataclass(frozen=True, slots=True)
class QualityVerdict:
    passed: bool
    reasons: tuple[RejectionReason, ...]


def assess(report: QualityReport, thresholds: QualityThresholds) -> QualityVerdict:
    reasons: list[RejectionReason] = []
    if report.score < thresholds.min_score:
        reasons.append(RejectionReason.TOO_BLURRY)
    if report.blur > thresholds.max_blur:
        reasons.append(RejectionReason.TOO_BLURRY)
    if report.face_px < thresholds.min_face_px:
        reasons.append(RejectionReason.TOO_SMALL)
    if abs(report.yaw_deg) > thresholds.max_yaw_deg:
        reasons.append(RejectionReason.EXTREME_POSE)
    if not thresholds.min_brightness <= report.brightness <= thresholds.max_brightness:
        reasons.append(RejectionReason.LOW_LIGHT)
    deduped = tuple(dict.fromkeys(reasons))
    return QualityVerdict(passed=not deduped, reasons=deduped)
