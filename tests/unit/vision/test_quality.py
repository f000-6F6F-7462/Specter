from dataclasses import replace

from specter.entities.targets import QualityReport, RejectionReason
from specter.vision.quality import (
    RUNTIME_QUALITY_THRESHOLDS,
    QualityThresholds,
    assess_quality,
    score_quality,
)

GOOD_REPORT = QualityReport(
    detection_score_ratio=0.92,
    blur_ratio=0.1,
    face_height_pixels=160,
    yaw_degrees=8.0,
    brightness_ratio=0.55,
)


def test_face_passes_when_every_measurement_is_within_limits() -> None:
    verdict = assess_quality(GOOD_REPORT, QualityThresholds())

    assert verdict.has_passed
    assert verdict.rejection_reasons == ()


def test_low_detection_score_is_reported_as_such_when_below_minimum() -> None:
    verdict = assess_quality(replace(GOOD_REPORT, detection_score_ratio=0.2), QualityThresholds())

    assert verdict.rejection_reasons == (RejectionReason.LOW_DETECTION_SCORE,)


def test_dark_and_bright_faces_get_different_reasons_when_rejected() -> None:
    dark_verdict = assess_quality(replace(GOOD_REPORT, brightness_ratio=0.05), QualityThresholds())
    bright_verdict = assess_quality(
        replace(GOOD_REPORT, brightness_ratio=0.98), QualityThresholds()
    )

    assert dark_verdict.rejection_reasons == (RejectionReason.TOO_DARK,)
    assert bright_verdict.rejection_reasons == (RejectionReason.TOO_BRIGHT,)


def test_every_failed_limit_is_reported_when_several_fail() -> None:
    poor_report = replace(GOOD_REPORT, blur_ratio=0.95, face_height_pixels=20, yaw_degrees=-70.0)

    verdict = assess_quality(poor_report, RUNTIME_QUALITY_THRESHOLDS)

    assert not verdict.has_passed
    assert verdict.rejection_reasons == (
        RejectionReason.TOO_BLURRY,
        RejectionReason.TOO_SMALL,
        RejectionReason.EXTREME_POSE,
    )


def test_sharp_frontal_face_scores_higher_than_blurry_one() -> None:
    blurry_report = replace(GOOD_REPORT, blur_ratio=0.7)

    assert score_quality(GOOD_REPORT) > score_quality(blurry_report)


def test_turned_face_scores_lower_than_frontal_one() -> None:
    turned_report = replace(GOOD_REPORT, yaw_degrees=60.0)

    assert score_quality(turned_report) < score_quality(GOOD_REPORT)


def test_score_stays_within_ratio_bounds_when_face_is_perfect() -> None:
    perfect_report = replace(
        GOOD_REPORT, detection_score_ratio=1.0, blur_ratio=0.0, yaw_degrees=0.0
    )

    assert score_quality(perfect_report) == 1.0
