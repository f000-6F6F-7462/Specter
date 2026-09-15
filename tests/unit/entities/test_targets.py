import pytest

from specter.core.errors import InvalidEntityError, NotFoundError
from specter.entities.targets import (
    EmbeddingModality,
    EnrollmentStatus,
    ImageStatus,
    ReferenceImage,
    Target,
)
from specter.entities.watchlists import TargetType
from specter.vision.quality import QualityReport, RejectionReason

GOOD_QUALITY = QualityReport(
    detection_score_ratio=0.92,
    blur_ratio=0.1,
    face_height_pixels=160,
    yaw_degrees=8.0,
    brightness_ratio=0.55,
)


def build_image(image_id: str, status: ImageStatus = ImageStatus.PENDING) -> ReferenceImage:
    return ReferenceImage(id=image_id, image_path=f"reference_images/{image_id}.jpg", status=status)


def build_target(*images: ReferenceImage) -> Target:
    return Target(
        id="target_jane",
        watchlist_id="watchlist_visitors",
        label="Jane",
        target_type=TargetType.PERSON,
        reference_images=images,
    )


@pytest.mark.parametrize(
    ("image_statuses", "expected_status"),
    [
        ((), EnrollmentStatus.QUEUED),
        ((ImageStatus.PENDING,), EnrollmentStatus.QUEUED),
        ((ImageStatus.EMBEDDED,), EnrollmentStatus.READY),
        ((ImageStatus.EMBEDDED, ImageStatus.PENDING), EnrollmentStatus.PARTIAL),
        ((ImageStatus.EMBEDDED, ImageStatus.REJECTED), EnrollmentStatus.PARTIAL),
        ((ImageStatus.REJECTED, ImageStatus.REJECTED), EnrollmentStatus.FAILED),
        ((ImageStatus.REJECTED, ImageStatus.PENDING), EnrollmentStatus.QUEUED),
    ],
)
def test_enrollment_status_summarizes_images_when_computed(
    image_statuses: tuple[ImageStatus, ...], expected_status: EnrollmentStatus
) -> None:
    images = [build_image(f"image_{index}", status) for index, status in enumerate(image_statuses)]

    assert build_target(*images).enrollment_status is expected_status


def test_mark_embedded_clears_previous_rejection_when_called() -> None:
    rejected_image = build_image("image_1").mark_rejected(RejectionReason.TOO_BLURRY, None)

    embedded_image = rejected_image.mark_embedded(GOOD_QUALITY, model_version="arcface-r100")

    assert embedded_image.status is ImageStatus.EMBEDDED
    assert embedded_image.rejection_reason is None
    assert embedded_image.model_version == "arcface-r100"


def test_with_reference_image_replaces_image_when_id_already_exists() -> None:
    target = build_target(build_image("image_1"), build_image("image_2"))

    updated_target = target.with_reference_image(build_image("image_1", ImageStatus.EMBEDDED))

    assert len(updated_target.reference_images) == 2
    updated_image = updated_target.find_reference_image("image_1")
    assert updated_image is not None
    assert updated_image.status is ImageStatus.EMBEDDED


def test_without_reference_image_fails_when_image_does_not_exist() -> None:
    with pytest.raises(NotFoundError):
        build_target(build_image("image_1")).without_reference_image("image_missing")


def test_target_is_rejected_when_reference_image_ids_repeat() -> None:
    with pytest.raises(InvalidEntityError, match="duplicates"):
        build_target(build_image("image_1"), build_image("image_1"))


def test_person_is_recognized_by_face_and_appearance_when_enrolled() -> None:
    assert build_target().embedding_modalities == (
        EmbeddingModality.FACE,
        EmbeddingModality.APPEARANCE,
    )
