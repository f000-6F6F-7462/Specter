"""Reading and writing targets, their reference images and the enrollment of each embedding."""

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from peewee import Expression, IntegrityError

from specter.core.errors import InvalidEntityError, NotFoundError
from specter.entities.targets import (
    EmbeddingModality,
    ImageEmbedding,
    ImageStatus,
    QualityReport,
    ReferenceImage,
    RejectionReason,
    Target,
    TargetType,
)
from specter.storage.columns import dump_json, format_utc_timestamp, load_json
from specter.storage.database import database_proxy
from specter.storage.queries import INSERTION_ORDER, delete_rows
from specter.storage.tables import (
    ImageEmbeddingRecord,
    ReferenceImageRecord,
    TargetRecord,
    WatchlistRecord,
)


@dataclass(frozen=True, slots=True)
class ImageEnrollment:
    """One kind of embedding of a reference image, with what enrolling it needs to know."""

    owner_id: str
    watchlist_id: str
    target_id: str
    is_target_enabled: bool
    reference_image_id: str
    # Relative to the data directory.
    image_path: str
    embedding: ImageEmbedding


def save_target(target: Target) -> None:
    """Inserts the target, or replaces its stored fields and reference images if it exists.

    Reference images and embeddings that the target no longer has are deleted.

    Raises:
        InvalidEntityError: The target's watchlist does not exist.
    """
    saved_at = format_utc_timestamp(datetime.now(UTC))
    record_values = {
        "watchlist_id": target.watchlist_id,
        "label": target.label,
        "target_type": target.target_type.value,
        "is_enabled": target.is_enabled,
        "metadata_json": dump_json(dict(target.metadata)),
        "enrollment_batch_id": target.enrollment_batch_id,
    }
    try:
        with database_proxy.atomic():
            updated_row_count = (
                TargetRecord.update(**record_values).where(TargetRecord.id == target.id).execute()
            )
            if updated_row_count == 0:
                TargetRecord.insert(id=target.id, created_at=saved_at, **record_values).execute()
            delete_rows(
                ReferenceImageRecord,
                ReferenceImageRecord.target_id == target.id,
                ReferenceImageRecord.id.not_in([image.id for image in target.reference_images]),
            )
            for image in target.reference_images:
                _save_reference_image(target.id, image, saved_at)
    except IntegrityError as error:
        raise InvalidEntityError(
            f"target {target.id} belongs to a watchlist that does not exist"
        ) from error


def find_target(target_id: str) -> Target | None:
    """Returns the target, or None if it does not exist."""
    record = TargetRecord.get_or_none(TargetRecord.id == target_id)
    return _build_targets([record])[0] if record is not None else None


def get_target(target_id: str) -> Target:
    """Returns the target.

    Raises:
        NotFoundError: The target does not exist.
    """
    target = find_target(target_id)
    if target is None:
        raise NotFoundError(f"target {target_id} does not exist")
    return target


def list_watchlist_targets(watchlist_id: str) -> list[Target]:
    """Returns the watchlist's targets, oldest first."""
    records = (
        TargetRecord.select()
        .where(TargetRecord.watchlist_id == watchlist_id)
        .order_by(TargetRecord.created_at, INSERTION_ORDER)
    )
    return _build_targets(list(records))


def list_enrollment_batch_targets(enrollment_batch_id: str) -> list[Target]:
    """Returns the targets enrolled together in one batch, oldest first."""
    records = (
        TargetRecord.select()
        .where(TargetRecord.enrollment_batch_id == enrollment_batch_id)
        .order_by(TargetRecord.created_at, INSERTION_ORDER)
    )
    return _build_targets(list(records))


def delete_target(target_id: str) -> None:
    """Deletes the target together with its reference images.

    Raises:
        NotFoundError: The target does not exist.
    """
    if delete_rows(TargetRecord, TargetRecord.id == target_id) == 0:
        raise NotFoundError(f"target {target_id} does not exist")


def list_embedded_image_enabled_states() -> dict[tuple[str, EmbeddingModality], bool]:
    """Returns every embedded image and modality with whether its target is enabled.

    This is exactly what the vector index should contain, so it is compared with Qdrant at startup.
    """
    return {
        (enrollment.reference_image_id, enrollment.embedding.modality): (
            enrollment.is_target_enabled
        )
        for enrollment in _select_image_enrollments(
            ImageEmbeddingRecord.status == ImageStatus.EMBEDDED.value
        )
    }


def find_image_enrollment(
    reference_image_id: str, modality: EmbeddingModality
) -> ImageEnrollment | None:
    """Returns one kind of embedding of the image, or None if the image or embedding is gone."""
    enrollments = _select_image_enrollments(
        ImageEmbeddingRecord.reference_image_id == reference_image_id,
        ImageEmbeddingRecord.modality == modality.value,
    )
    return enrollments[0] if enrollments else None


def list_pending_image_enrollments() -> list[ImageEnrollment]:
    """Returns every embedding that waits to be enrolled, oldest image first."""
    return _select_image_enrollments(ImageEmbeddingRecord.status == ImageStatus.PENDING.value)


def save_image_embedding(reference_image_id: str, embedding: ImageEmbedding) -> bool:
    """Stores the enrollment outcome of one kind of embedding of an existing image.

    Returns False when the image or that kind of embedding no longer exists, such as after its
    target was deleted while it was being enrolled.
    """
    updated_row_count = (
        ImageEmbeddingRecord.update(**_build_embedding_values(embedding))
        .where(
            ImageEmbeddingRecord.reference_image_id == reference_image_id,
            ImageEmbeddingRecord.modality == embedding.modality.value,
        )
        .execute()
    )
    return bool(updated_row_count)


def reset_outdated_image_embeddings(
    model_versions_by_modality: Mapping[EmbeddingModality, str],
) -> int:
    """Marks embeddings made by another model than the current one as pending again.

    Embeddings of different models do not compare, so these must be enrolled again before they can
    match. Returns how many were reset.
    """
    reset_count = 0
    for modality, model_version in model_versions_by_modality.items():
        reset_count += (
            ImageEmbeddingRecord.update(
                status=ImageStatus.PENDING.value,
                quality_json=None,
                rejection_reason=None,
                model_version=None,
            )
            .where(
                ImageEmbeddingRecord.modality == modality.value,
                ImageEmbeddingRecord.status == ImageStatus.EMBEDDED.value,
                ImageEmbeddingRecord.model_version != model_version,
            )
            .execute()
        )
    return reset_count


def _save_reference_image(target_id: str, image: ReferenceImage, saved_at: str) -> None:
    updated_row_count = (
        ReferenceImageRecord.update(target_id=target_id, image_path=image.image_path)
        .where(ReferenceImageRecord.id == image.id)
        .execute()
    )
    if updated_row_count == 0:
        ReferenceImageRecord.insert(
            id=image.id, target_id=target_id, image_path=image.image_path, created_at=saved_at
        ).execute()
    delete_rows(
        ImageEmbeddingRecord,
        ImageEmbeddingRecord.reference_image_id == image.id,
        ImageEmbeddingRecord.modality.not_in(
            [embedding.modality.value for embedding in image.embeddings]
        ),
    )
    for embedding in image.embeddings:
        ImageEmbeddingRecord.insert(
            reference_image_id=image.id,
            modality=embedding.modality.value,
            **_build_embedding_values(embedding),
        ).on_conflict_replace().execute()


def _build_embedding_values(embedding: ImageEmbedding) -> dict[str, object]:
    return {
        "status": embedding.status.value,
        "quality_json": dump_json(asdict(embedding.quality)) if embedding.quality else None,
        "rejection_reason": (
            embedding.rejection_reason.value if embedding.rejection_reason else None
        ),
        "model_version": embedding.model_version,
    }


def _select_image_enrollments(*conditions: Expression) -> list[ImageEnrollment]:
    embedding_records = list(ImageEmbeddingRecord.select().where(*conditions))
    image_records_by_id = {
        record.id: record
        for record in ReferenceImageRecord.select().where(
            ReferenceImageRecord.id.in_(
                list({record.reference_image_id for record in embedding_records})
            )
        )
    }
    target_records_by_id = {
        record.id: record
        for record in TargetRecord.select().where(
            TargetRecord.id.in_(list({record.target_id for record in image_records_by_id.values()}))
        )
    }
    owner_ids_by_watchlist_id = {
        record.id: record.owner_id
        for record in WatchlistRecord.select().where(
            WatchlistRecord.id.in_(
                list({record.watchlist_id for record in target_records_by_id.values()})
            )
        )
    }
    enrollments: list[ImageEnrollment] = []
    for embedding_record in embedding_records:
        image_record = image_records_by_id[embedding_record.reference_image_id]
        target_record = target_records_by_id[image_record.target_id]
        enrollments.append(
            ImageEnrollment(
                owner_id=owner_ids_by_watchlist_id[target_record.watchlist_id],
                watchlist_id=target_record.watchlist_id,
                target_id=target_record.id,
                is_target_enabled=target_record.is_enabled,
                reference_image_id=image_record.id,
                image_path=image_record.image_path,
                embedding=_build_embedding(embedding_record),
            )
        )
    # Images enrolled first were added first, which keeps a batch in the order it was uploaded.
    return sorted(
        enrollments,
        key=lambda enrollment: (
            image_records_by_id[enrollment.reference_image_id].created_at,
            enrollment.embedding.modality,
        ),
    )


def _build_targets(records: list[TargetRecord]) -> list[Target]:
    images_by_target_id = _load_reference_images_by_target_id(record.id for record in records)
    return [
        Target(
            id=record.id,
            watchlist_id=record.watchlist_id,
            label=record.label,
            target_type=TargetType(record.target_type),
            reference_images=images_by_target_id.get(record.id, ()),
            is_enabled=record.is_enabled,
            metadata=load_json(record.metadata_json),
            enrollment_batch_id=record.enrollment_batch_id,
        )
        for record in records
    ]


def _load_reference_images_by_target_id(
    target_ids: Iterable[str],
) -> dict[str, tuple[ReferenceImage, ...]]:
    image_records = list(
        ReferenceImageRecord.select()
        .where(ReferenceImageRecord.target_id.in_(list(target_ids)))
        .order_by(ReferenceImageRecord.created_at, INSERTION_ORDER)
    )
    embeddings_by_image_id: defaultdict[str, list[ImageEmbedding]] = defaultdict(list)
    embedding_records = (
        ImageEmbeddingRecord.select()
        .where(ImageEmbeddingRecord.reference_image_id.in_([record.id for record in image_records]))
        .order_by(ImageEmbeddingRecord.modality)
    )
    for embedding_record in embedding_records:
        embeddings_by_image_id[embedding_record.reference_image_id].append(
            _build_embedding(embedding_record)
        )
    images_by_target_id: defaultdict[str, list[ReferenceImage]] = defaultdict(list)
    for record in image_records:
        images_by_target_id[record.target_id].append(
            ReferenceImage(
                id=record.id,
                image_path=record.image_path,
                embeddings=tuple(embeddings_by_image_id[record.id]),
            )
        )
    return {target_id: tuple(images) for target_id, images in images_by_target_id.items()}


def _build_embedding(record: ImageEmbeddingRecord) -> ImageEmbedding:
    return ImageEmbedding(
        modality=EmbeddingModality(record.modality),
        status=ImageStatus(record.status),
        quality=(
            QualityReport(**load_json(record.quality_json))
            if record.quality_json is not None
            else None
        ),
        rejection_reason=(
            RejectionReason(record.rejection_reason) if record.rejection_reason else None
        ),
        model_version=record.model_version,
    )
