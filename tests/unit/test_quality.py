import pytest

from specter.domain.quality import (
    ENROLLMENT_THRESHOLDS,
    RUNTIME_THRESHOLDS,
    QualityReport,
    RejectionReason,
    assess,
)

GOOD = QualityReport(score=0.9, blur=0.1, face_px=160, yaw_deg=10.0, brightness=0.55)


def test_good_report_passes() -> None:
    verdict = assess(GOOD, ENROLLMENT_THRESHOLDS)
    assert verdict.passed is True
    assert not verdict.reasons


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("score", 0.2, RejectionReason.TOO_BLURRY),
        ("blur", 0.9, RejectionReason.TOO_BLURRY),
        ("face_px", 20, RejectionReason.TOO_SMALL),
        ("yaw_deg", 70.0, RejectionReason.EXTREME_POSE),
        ("yaw_deg", -70.0, RejectionReason.EXTREME_POSE),
        ("brightness", 0.05, RejectionReason.LOW_LIGHT),
        ("brightness", 0.98, RejectionReason.LOW_LIGHT),
    ],
)
def test_single_defect_is_reported(field: str, value: float, reason: RejectionReason) -> None:
    report = QualityReport(**{**vars_of(GOOD), field: value})  # type: ignore[arg-type]
    verdict = assess(report, ENROLLMENT_THRESHOLDS)
    assert verdict.passed is False
    assert reason in verdict.reasons


def test_reasons_are_deduped() -> None:
    # low score and high blur both map to TOO_BLURRY — reported once.
    report = QualityReport(score=0.1, blur=0.95, face_px=160, yaw_deg=5.0, brightness=0.5)
    verdict = assess(report, ENROLLMENT_THRESHOLDS)
    assert verdict.reasons.count(RejectionReason.TOO_BLURRY) == 1


def test_runtime_thresholds_are_looser() -> None:
    borderline = QualityReport(score=0.45, blur=0.7, face_px=34, yaw_deg=50.0, brightness=0.5)
    assert assess(borderline, ENROLLMENT_THRESHOLDS).passed is False
    assert assess(borderline, RUNTIME_THRESHOLDS).passed is True


def vars_of(report: QualityReport) -> dict[str, float]:
    return {
        "score": report.score,
        "blur": report.blur,
        "face_px": report.face_px,
        "yaw_deg": report.yaw_deg,
        "brightness": report.brightness,
    }
