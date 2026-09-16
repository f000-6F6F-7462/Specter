"""Targets and the reference images they are recognized from."""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum

from specter.core.errors import NotFoundError
from specter.entities.validation import require_non_empty_text, require_unique


class TargetType(StrEnum):
    """What kind of thing a target is."""

    PERSON = "person"
    VEHICLE = "vehicle"
    OBJECT = "object"


class EmbeddingModality(StrEnum):
    """What an embedding describes."""

    FACE = "face"
    APPEARANCE = "appearance"


class ImageStatus(StrEnum):
    """Where a reference image is in enrollment."""

    PENDING = "pending"
    EMBEDDED = "embedded"
    REJECTED = "rejected"


class EnrollmentStatus(StrEnum):
    """Enrollment progress of a target, summarized from its reference images."""

    QUEUED = "queued"
    PARTIAL = "partial"
    READY = "ready"
    FAILED = "failed"


class RejectionReason(StrEnum):
    """Why an image was not used for matching."""

    NO_FACE_DETECTED = "no_face_detected"
    MULTIPLE_FACES = "multiple_faces"
    LOW_DETECTION_SCORE = "low_detection_score"
    TOO_SMALL = "too_small"
    TOO_BLURRY = "too_blurry"
    EXTREME_POSE = "extreme_pose"
    TOO_DARK = "too_dark"
    TOO_BRIGHT = "too_bright"
    # Enrollment kept failing for a reason unrelated to the image, such as a detector error.
    PROCESSING_FAILED = "processing_failed"


@dataclass(frozen=True, slots=True)
class QualityReport:
    """Measurements of one face crop."""

    detection_score_ratio: float
    # 0 is perfectly sharp and 1 is completely blurred.
    blur_ratio: float
    face_height_pixels: int
    yaw_degrees: float
    brightness_ratio: float


# Objects are found by rules rather than recognized as individuals, so they have no embeddings.
EMBEDDING_MODALITIES_BY_TARGET_TYPE: Mapping[TargetType, tuple[EmbeddingModality, ...]] = {
    TargetType.PERSON: (EmbeddingModality.FACE, EmbeddingModality.APPEARANCE),
    TargetType.VEHICLE: (EmbeddingModality.APPEARANCE,),
    TargetType.OBJECT: (),
}


@dataclass(frozen=True, slots=True)
class ReferenceImage:
    """A photo of a target that is embedded for matching."""

    id: str
    # Relative to the data directory, so the data directory can move between devices.
    image_path: str
    status: ImageStatus = ImageStatus.PENDING
    quality: QualityReport | None = None
    rejection_reason: RejectionReason | None = None
    model_version: str | None = None

    def mark_embedded(self, quality: QualityReport, model_version: str) -> "ReferenceImage":
        """Returns a copy marked as embedded by the given model."""
        return replace(
            self,
            status=ImageStatus.EMBEDDED,
            quality=quality,
            rejection_reason=None,
            model_version=model_version,
        )

    def mark_rejected(
        self, reason: RejectionReason, quality: QualityReport | None
    ) -> "ReferenceImage":
        """Returns a copy marked as rejected for the given reason."""
        return replace(self, status=ImageStatus.REJECTED, quality=quality, rejection_reason=reason)


@dataclass(frozen=True, slots=True)
class Target:
    """A person, vehicle or object that a watchlist looks for."""

    id: str
    watchlist_id: str
    label: str
    target_type: TargetType
    reference_images: tuple[ReferenceImage, ...] = ()
    is_enabled: bool = True
    metadata: Mapping[str, object] = field(default_factory=dict)
    enrollment_batch_id: str | None = None

    def __post_init__(self) -> None:
        require_non_empty_text(self.label, "target label")
        require_unique([image.id for image in self.reference_images], "reference image ids")

    @property
    def embedding_modalities(self) -> tuple[EmbeddingModality, ...]:
        """The kinds of embeddings this target is recognized by."""
        return EMBEDDING_MODALITIES_BY_TARGET_TYPE[self.target_type]

    @property
    def embedded_images(self) -> tuple[ReferenceImage, ...]:
        """Reference images that are ready for matching."""
        return tuple(
            image for image in self.reference_images if image.status is ImageStatus.EMBEDDED
        )

    @property
    def enrollment_status(self) -> EnrollmentStatus:
        """Summarizes the reference images into the target's enrollment progress.

        Ready when every image is embedded, partial when only some are, failed when all were
        rejected, and queued otherwise.
        """
        if not self.reference_images:
            return EnrollmentStatus.QUEUED
        embedded_count = len(self.embedded_images)
        if embedded_count == len(self.reference_images):
            return EnrollmentStatus.READY
        if embedded_count > 0:
            return EnrollmentStatus.PARTIAL
        if all(image.status is ImageStatus.REJECTED for image in self.reference_images):
            return EnrollmentStatus.FAILED
        return EnrollmentStatus.QUEUED

    def find_reference_image(self, image_id: str) -> ReferenceImage | None:
        """Returns the reference image with the id, or None if the target has none."""
        return next((image for image in self.reference_images if image.id == image_id), None)

    def with_reference_image(self, image: ReferenceImage) -> "Target":
        """Returns a copy with the image added, replacing an existing image with the same id."""
        other_images = tuple(
            existing_image
            for existing_image in self.reference_images
            if existing_image.id != image.id
        )
        return replace(self, reference_images=(*other_images, image))

    def without_reference_image(self, image_id: str) -> "Target":
        """Returns a copy without the image.

        Raises:
            NotFoundError: The target has no image with that id.
        """
        if self.find_reference_image(image_id) is None:
            raise NotFoundError(f"target {self.id} has no reference image {image_id}")
        remaining_images = tuple(image for image in self.reference_images if image.id != image_id)
        return replace(self, reference_images=remaining_images)
