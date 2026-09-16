import pytest

from specter.core.errors import InvalidEntityError
from specter.entities.targets import (
    EmbeddingModality,
    ImageEmbedding,
    QualityReport,
    ReferenceImage,
    RejectionReason,
    Target,
    TargetType,
)
from specter.entities.watchlists import Watchlist
from specter.storage.targets import (
    find_image_enrollment,
    find_target,
    list_embedded_image_enabled_states,
    list_enrollment_batch_targets,
    list_pending_image_enrollments,
    reset_outdated_image_embeddings,
    save_image_embedding,
    save_target,
)
from specter.storage.watchlists import delete_watchlist, save_watchlist

# The database comes first, so the watchlist is saved into this test's own database.
pytestmark = pytest.mark.usefixtures("database", "visitors_watchlist")

WATCHLIST_ID = "watchlist_visitors"
GOOD_QUALITY = QualityReport(
    detection_score_ratio=0.92,
    blur_ratio=0.1,
    face_height_pixels=160,
    yaw_degrees=8.0,
    brightness_ratio=0.55,
)


@pytest.fixture
def visitors_watchlist() -> None:
    save_watchlist(
        Watchlist(
            id=WATCHLIST_ID, owner_id="owner_alice", name="Visitors", target_type=TargetType.PERSON
        )
    )


def build_target(target_id: str, *images: ReferenceImage, is_enabled: bool = True) -> Target:
    return Target(
        id=target_id,
        watchlist_id=WATCHLIST_ID,
        label=f"Label {target_id}",
        target_type=TargetType.PERSON,
        reference_images=images,
        is_enabled=is_enabled,
        metadata={"department": "sales"},
        enrollment_batch_id="enrollment_batch_1",
    )


def build_image(image_id: str, *embeddings: ImageEmbedding) -> ReferenceImage:
    return ReferenceImage(
        id=image_id, image_path=f"reference_images/{image_id}.jpg", embeddings=embeddings
    )


def pending(modality: EmbeddingModality) -> ImageEmbedding:
    return ImageEmbedding(modality)


def embedded(modality: EmbeddingModality, model_version: str = "arcface_r50") -> ImageEmbedding:
    return ImageEmbedding(modality).mark_embedded(GOOD_QUALITY, model_version)


def test_target_with_images_is_unchanged_when_saved_and_loaded() -> None:
    target = build_target(
        "target_jane",
        build_image("image_pending", pending(EmbeddingModality.FACE)),
        build_image(
            "image_mixed",
            embedded(EmbeddingModality.APPEARANCE, "osnet_x0_25"),
            ImageEmbedding(EmbeddingModality.FACE).mark_rejected(
                RejectionReason.TOO_BLURRY, GOOD_QUALITY
            ),
        ),
    )

    save_target(target)

    assert find_target("target_jane") == target


def test_images_the_target_no_longer_has_are_deleted_when_saved_again() -> None:
    save_target(build_target("target_jane", build_image("image_1"), build_image("image_2")))

    save_target(build_target("target_jane", build_image("image_2")))

    target = find_target("target_jane")
    assert target is not None
    assert [image.id for image in target.reference_images] == ["image_2"]


def test_target_is_rejected_when_its_watchlist_does_not_exist() -> None:
    target = Target(
        id="target_orphan",
        watchlist_id="watchlist_missing",
        label="Orphan",
        target_type=TargetType.PERSON,
    )

    with pytest.raises(InvalidEntityError, match="watchlist that does not exist"):
        save_target(target)


def test_targets_are_deleted_when_their_watchlist_is_deleted() -> None:
    save_target(build_target("target_jane", build_image("image_1")))

    delete_watchlist(WATCHLIST_ID)

    assert find_target("target_jane") is None


def test_only_embedded_modalities_are_listed_with_target_state_when_listing_enabled_states() -> (
    None
):
    save_target(
        build_target(
            "target_jane",
            build_image(
                "image_jane",
                embedded(EmbeddingModality.FACE),
                pending(EmbeddingModality.APPEARANCE),
            ),
        )
    )
    save_target(
        build_target(
            "target_bob",
            build_image("image_bob", embedded(EmbeddingModality.FACE)),
            is_enabled=False,
        )
    )

    enabled_states = list_embedded_image_enabled_states()

    assert enabled_states == {
        ("image_jane", EmbeddingModality.FACE): True,
        ("image_bob", EmbeddingModality.FACE): False,
    }


def test_pending_enrollments_carry_owner_and_image_when_listed() -> None:
    save_target(
        build_target(
            "target_jane",
            build_image(
                "image_jane",
                embedded(EmbeddingModality.FACE),
                pending(EmbeddingModality.APPEARANCE),
            ),
        )
    )

    enrollments = list_pending_image_enrollments()

    assert [
        (enrollment.owner_id, enrollment.reference_image_id, enrollment.embedding.modality)
        for enrollment in enrollments
    ] == [("owner_alice", "image_jane", EmbeddingModality.APPEARANCE)]
    assert enrollments[0].image_path == "reference_images/image_jane.jpg"


def test_enrollment_outcome_is_stored_when_the_image_still_exists() -> None:
    save_target(
        build_target("target_jane", build_image("image_jane", pending(EmbeddingModality.FACE)))
    )

    was_saved = save_image_embedding("image_jane", embedded(EmbeddingModality.FACE))
    was_saved_for_missing_image = save_image_embedding(
        "image_missing", embedded(EmbeddingModality.FACE)
    )

    enrollment = find_image_enrollment("image_jane", EmbeddingModality.FACE)
    assert (was_saved, was_saved_for_missing_image) == (True, False)
    assert enrollment is not None
    assert enrollment.embedding == embedded(EmbeddingModality.FACE)


def test_embeddings_of_another_model_become_pending_when_reset() -> None:
    save_target(
        build_target(
            "target_jane",
            build_image(
                "image_jane",
                embedded(EmbeddingModality.FACE, "arcface_mobilefacenet"),
                embedded(EmbeddingModality.APPEARANCE, "osnet_x0_25"),
            ),
        )
    )

    reset_count = reset_outdated_image_embeddings(
        {EmbeddingModality.FACE: "arcface_r50", EmbeddingModality.APPEARANCE: "osnet_x0_25"}
    )

    assert reset_count == 1
    assert [enrollment.embedding.modality for enrollment in list_pending_image_enrollments()] == [
        EmbeddingModality.FACE
    ]


def test_targets_of_a_batch_are_listed_when_enrolled_together() -> None:
    save_target(build_target("target_jane"))
    save_target(build_target("target_bob"))

    batch_targets = list_enrollment_batch_targets("enrollment_batch_1")

    assert [target.id for target in batch_targets] == ["target_jane", "target_bob"]
    assert batch_targets[0].reference_images == ()
