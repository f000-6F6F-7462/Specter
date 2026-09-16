from dataclasses import replace

import pytest

from specter.core.errors import InvalidEntityError, NotFoundError
from specter.entities.targets import (
    EmbeddingModality,
    EnrollmentStatus,
    ImageEmbedding,
    ImageStatus,
    QualityReport,
    ReferenceImage,
    RejectionReason,
    Target,
    TargetType,
)

GOOD_QUALITY = QualityReport(
    detection_score_ratio=0.92,
    blur_ratio=0.1,
    face_height_pixels=160,
    yaw_degrees=8.0,
    brightness_ratio=0.55,
)


def build_image(image_id: str, *statuses: ImageStatus) -> ReferenceImage:
    return ReferenceImage(
        id=image_id,
        image_path=f"reference_images/{image_id}.jpg",
        embeddings=tuple(
            ImageEmbedding(modality, status)
            for modality, status in zip(EmbeddingModality, statuses, strict=False)
        ),
    )


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
        (((ImageStatus.PENDING, ImageStatus.PENDING),), EnrollmentStatus.QUEUED),
        (((ImageStatus.EMBEDDED, ImageStatus.EMBEDDED),), EnrollmentStatus.READY),
        (((ImageStatus.REJECTED, ImageStatus.EMBEDDED),), EnrollmentStatus.PARTIAL),
        (((ImageStatus.EMBEDDED,), (ImageStatus.PENDING,)), EnrollmentStatus.PARTIAL),
        (((ImageStatus.REJECTED, ImageStatus.REJECTED),), EnrollmentStatus.FAILED),
        (((ImageStatus.REJECTED, ImageStatus.PENDING),), EnrollmentStatus.QUEUED),
    ],
)
def test_enrollment_status_summarizes_every_embedding_when_computed(
    image_statuses: tuple[tuple[ImageStatus, ...], ...], expected_status: EnrollmentStatus
) -> None:
    images = [
        build_image(f"image_{index}", *statuses) for index, statuses in enumerate(image_statuses)
    ]

    assert build_target(*images).enrollment_status is expected_status


def test_mark_embedded_clears_previous_rejection_when_called() -> None:
    rejected_embedding = ImageEmbedding(EmbeddingModality.FACE).mark_rejected(
        RejectionReason.TOO_BLURRY, None
    )

    embedded_embedding = rejected_embedding.mark_embedded(GOOD_QUALITY, model_version="arcface-r50")

    assert embedded_embedding.status is ImageStatus.EMBEDDED
    assert embedded_embedding.rejection_reason is None
    assert embedded_embedding.model_version == "arcface-r50"


def test_new_image_waits_for_every_modality_of_the_target() -> None:
    image = build_target().create_reference_image("image_1", "reference_images/image_1.jpg")

    assert [(embedding.modality, embedding.status) for embedding in image.embeddings] == [
        (EmbeddingModality.FACE, ImageStatus.PENDING),
        (EmbeddingModality.APPEARANCE, ImageStatus.PENDING),
    ]


def test_with_reference_image_replaces_image_when_id_already_exists() -> None:
    target = build_target(build_image("image_1"), build_image("image_2"))

    updated_target = target.with_reference_image(build_image("image_1", ImageStatus.EMBEDDED))

    assert len(updated_target.reference_images) == 2
    updated_image = updated_target.find_reference_image("image_1")
    assert updated_image is not None
    assert updated_image.embeddings[0].status is ImageStatus.EMBEDDED


def test_without_reference_image_fails_when_image_does_not_exist() -> None:
    with pytest.raises(NotFoundError):
        build_target(build_image("image_1")).without_reference_image("image_missing")


def test_target_is_rejected_when_reference_image_ids_repeat() -> None:
    with pytest.raises(InvalidEntityError, match="duplicates"):
        build_target(build_image("image_1"), build_image("image_1"))


def test_vehicle_has_no_embeddings_because_rules_find_it() -> None:
    vehicle = replace(build_target(), target_type=TargetType.VEHICLE)

    assert vehicle.embedding_modalities == ()


def test_person_is_recognized_by_face_and_appearance_when_enrolled() -> None:
    assert build_target().embedding_modalities == (
        EmbeddingModality.FACE,
        EmbeddingModality.APPEARANCE,
    )
