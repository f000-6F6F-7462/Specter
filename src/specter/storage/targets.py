"""Reading and writing targets and their reference images."""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict
from datetime import UTC, datetime

from peewee import IntegrityError

from specter.core.errors import InvalidEntityError, NotFoundError
from specter.entities.targets import (
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
from specter.storage.tables import ReferenceImageRecord, TargetRecord


def save_target(target: Target) -> None:
    """Inserts the target, or replaces its stored fields and reference images if it exists.

    Reference images that the target no longer has are deleted.

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


def list_embedded_image_enabled_states() -> dict[str, bool]:
    """Returns every embedded reference image id with whether its target is enabled.

    This is exactly what the vector index should contain, so it is compared with Qdrant at startup.
    """
    embedded_image_records = list(
        ReferenceImageRecord.select().where(
            ReferenceImageRecord.status == ImageStatus.EMBEDDED.value
        )
    )
    target_records = TargetRecord.select().where(
        TargetRecord.id.in_(list({record.target_id for record in embedded_image_records}))
    )
    is_enabled_by_target_id = {record.id: record.is_enabled for record in target_records}
    return {
        record.id: is_enabled_by_target_id[record.target_id] for record in embedded_image_records
    }


def _save_reference_image(target_id: str, image: ReferenceImage, saved_at: str) -> None:
    record_values = {
        "target_id": target_id,
        "image_path": image.image_path,
        "status": image.status.value,
        "quality_json": dump_json(asdict(image.quality)) if image.quality is not None else None,
        "rejection_reason": image.rejection_reason.value if image.rejection_reason else None,
        "model_version": image.model_version,
    }
    updated_row_count = (
        ReferenceImageRecord.update(**record_values)
        .where(ReferenceImageRecord.id == image.id)
        .execute()
    )
    if updated_row_count == 0:
        ReferenceImageRecord.insert(id=image.id, created_at=saved_at, **record_values).execute()


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
    images_by_target_id: defaultdict[str, list[ReferenceImage]] = defaultdict(list)
    records = (
        ReferenceImageRecord.select()
        .where(ReferenceImageRecord.target_id.in_(list(target_ids)))
        .order_by(ReferenceImageRecord.created_at, INSERTION_ORDER)
    )
    for record in records:
        images_by_target_id[record.target_id].append(_build_reference_image(record))
    return {target_id: tuple(images) for target_id, images in images_by_target_id.items()}


def _build_reference_image(record: ReferenceImageRecord) -> ReferenceImage:
    return ReferenceImage(
        id=record.id,
        image_path=record.image_path,
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
