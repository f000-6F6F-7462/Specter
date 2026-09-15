import pytest

from specter.core.errors import InvalidEntityError
from specter.entities.targets import (
    ImageStatus,
    QualityReport,
    ReferenceImage,
    RejectionReason,
    Target,
    TargetType,
)
from specter.entities.watchlists import Watchlist
from specter.storage.targets import (
    find_target,
    get_target,
    list_embedded_image_enabled_states,
    list_enrollment_batch_targets,
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


def build_image(image_id: str) -> ReferenceImage:
    return ReferenceImage(id=image_id, image_path=f"reference_images/{image_id}.jpg")


def test_target_with_images_is_unchanged_when_saved_and_loaded() -> None:
    target = build_target(
        "target_jane",
        build_image("image_pending"),
        build_image("image_embedded").mark_embedded(GOOD_QUALITY, model_version="arcface-r100"),
        build_image("image_rejected").mark_rejected(RejectionReason.TOO_BLURRY, GOOD_QUALITY),
    )

    save_target(target)

    assert get_target("target_jane") == target


def test_images_the_target_no_longer_has_are_deleted_when_saved_again() -> None:
    save_target(build_target("target_jane", build_image("image_1"), build_image("image_2")))

    save_target(build_target("target_jane", build_image("image_2")))

    assert [image.id for image in get_target("target_jane").reference_images] == ["image_2"]


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


def test_only_embedded_images_are_listed_with_target_state_when_listing_enabled_states() -> None:
    embedded_image = build_image("image_embedded").mark_embedded(GOOD_QUALITY, "arcface-r100")
    save_target(build_target("target_jane", embedded_image, build_image("image_pending")))
    save_target(
        build_target(
            "target_bob",
            build_image("image_bob").mark_embedded(GOOD_QUALITY, "arcface-r100"),
            is_enabled=False,
        )
    )

    enabled_states = list_embedded_image_enabled_states()

    assert enabled_states == {"image_embedded": True, "image_bob": False}


def test_targets_of_a_batch_are_listed_when_enrolled_together() -> None:
    save_target(build_target("target_jane"))
    save_target(build_target("target_bob"))

    batch_targets = list_enrollment_batch_targets("enrollment_batch_1")

    assert [target.id for target in batch_targets] == ["target_jane", "target_bob"]
    assert all(image.status is ImageStatus.PENDING for image in batch_targets[0].reference_images)
