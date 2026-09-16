"""Targets of a watchlist and their reference images, which the detector enrolls."""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import partial
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile, status
from nats.errors import Error as NatsError
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from specter.api.dependencies import ApiServices, ServicesDependency
from specter.api.errors import change_vector_index
from specter.api.notifications import publish_configuration_change, publish_when_connected
from specter.api.ownership import get_owner_target, get_owner_watchlist
from specter.api.schemas import RequestModel
from specter.core.errors import InvalidEntityError, NotFoundError
from specter.core.identifiers import new_identifier
from specter.entities.targets import (
    EmbeddingModality,
    EnrollmentStatus,
    ImageStatus,
    ReferenceImage,
    RejectionReason,
    Target,
    TargetType,
)
from specter.entities.watchlists import Watchlist
from specter.messaging.messages import ChangeKind, EnrollmentJobMessage, EntityKind
from specter.storage.targets import (
    delete_target,
    list_enrollment_batch_targets,
    list_watchlist_targets,
    save_target,
    save_targets,
)
from specter.vision.quality import score_quality

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/owners/{owner_id}", tags=["targets"])

FILE_SUFFIXES_BY_CONTENT_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


class TargetSpecificationBody(RequestModel):
    """One target of a batch, and the names of its uploaded image files."""

    label: str = Field(min_length=1)
    metadata: dict[str, object] = Field(default_factory=dict)
    image_file_names: list[str] = Field(default_factory=list)


class TargetUpdateBody(RequestModel):
    """Fields of a target to change; omitted fields keep their value."""

    label: str | None = Field(default=None, min_length=1)
    metadata: dict[str, object] | None = None
    is_enabled: bool | None = None


class ImageEmbeddingResponse(BaseModel):
    """Where one kind of embedding of a reference image is in enrollment."""

    modality: EmbeddingModality
    status: ImageStatus
    rejection_reason: RejectionReason | None
    quality_score_ratio: float | None


class ReferenceImageResponse(BaseModel):
    """A reference image and the enrollment of each kind of embedding taken from it."""

    id: str
    embeddings: list[ImageEmbeddingResponse]


class TargetResponse(BaseModel):
    """A target and its reference images."""

    id: str
    watchlist_id: str
    label: str
    target_type: TargetType
    is_enabled: bool
    metadata: dict[str, object]
    enrollment_batch_id: str | None
    enrollment_status: EnrollmentStatus
    reference_images: list[ReferenceImageResponse]


@dataclass(frozen=True, slots=True)
class _UploadedImage:
    file_name: str
    content: bytes
    file_suffix: str


TARGET_SPECIFICATIONS_ADAPTER = TypeAdapter(list[TargetSpecificationBody])


@router.post("/watchlists/{watchlist_id}/targets", status_code=status.HTTP_201_CREATED)
async def create_targets(
    owner_id: str,
    watchlist_id: str,
    targets: Annotated[str, Form(description="JSON list of target specifications")],
    services: ServicesDependency,
    images: Annotated[list[UploadFile] | None, File()] = None,
) -> list[TargetResponse]:
    """Creates a batch of targets with their reference images and queues them for enrollment.

    Every uploaded file must be named by exactly one target. Nothing is stored unless the whole
    batch is valid.

    Raises:
        InvalidEntityError: The specifications or files are invalid.
    """
    watchlist = await get_owner_watchlist(services, owner_id, watchlist_id)
    specifications = parse_target_specifications(targets)
    uploaded_images_by_name = await read_uploaded_images(services, images or [])
    named_file_names = [
        file_name
        for specification in specifications
        for file_name in specification.image_file_names
    ]
    if sorted(named_file_names) != sorted(uploaded_images_by_name):
        raise InvalidEntityError(
            "every uploaded file must be named by exactly one target, and every named file uploaded"
        )

    enrollment_batch_id = new_identifier("enrollment_batch")
    new_targets: list[Target] = []
    uploads_by_image_path: dict[str, bytes] = {}
    for specification in specifications:
        target = Target(
            id=new_identifier("target"),
            watchlist_id=watchlist.id,
            label=specification.label,
            target_type=watchlist.target_type,
            metadata=specification.metadata,
            enrollment_batch_id=enrollment_batch_id,
        )
        target, image_contents = add_uploaded_images(
            services,
            owner_id,
            target,
            [uploaded_images_by_name[file_name] for file_name in specification.image_file_names],
        )
        new_targets.append(target)
        uploads_by_image_path.update(image_contents)

    await write_images(services, uploads_by_image_path)
    try:
        await asyncio.get_running_loop().run_in_executor(
            services.database_thread.executor, partial(save_targets, new_targets)
        )
    except Exception:
        await delete_images(services, list(uploads_by_image_path))
        raise
    for target in new_targets:
        await announce_new_images(
            services, watchlist, target, target.reference_images, ChangeKind.CREATED
        )
    return [build_target_response(target) for target in new_targets]


@router.get("/watchlists/{watchlist_id}/targets")
async def list_targets(
    owner_id: str, watchlist_id: str, services: ServicesDependency
) -> list[TargetResponse]:
    """Lists the targets of the owner's watchlist, oldest first."""
    await get_owner_watchlist(services, owner_id, watchlist_id)
    targets = await asyncio.get_running_loop().run_in_executor(
        services.database_thread.executor, partial(list_watchlist_targets, watchlist_id)
    )
    return [build_target_response(target) for target in targets]


@router.get("/enrollment-batches/{enrollment_batch_id}")
async def read_enrollment_batch(
    owner_id: str, enrollment_batch_id: str, services: ServicesDependency
) -> list[TargetResponse]:
    """Returns the targets created together in one batch, to follow their enrollment."""
    targets = await asyncio.get_running_loop().run_in_executor(
        services.database_thread.executor,
        partial(list_enrollment_batch_targets, enrollment_batch_id),
    )
    for target in targets:
        await get_owner_watchlist(services, owner_id, target.watchlist_id)
    if not targets:
        raise NotFoundError(f"enrollment batch {enrollment_batch_id} does not exist")
    return [build_target_response(target) for target in targets]


@router.get("/watchlists/{watchlist_id}/targets/{target_id}")
async def read_target(
    owner_id: str, watchlist_id: str, target_id: str, services: ServicesDependency
) -> TargetResponse:
    """Returns a target of the owner's watchlist."""
    return build_target_response(
        await get_owner_target(services, owner_id, watchlist_id, target_id)
    )


@router.patch("/watchlists/{watchlist_id}/targets/{target_id}")
async def update_target(
    owner_id: str,
    watchlist_id: str,
    target_id: str,
    body: TargetUpdateBody,
    services: ServicesDependency,
) -> TargetResponse:
    """Changes the given fields; a disabled target stops matching at once."""
    target = await get_owner_target(services, owner_id, watchlist_id, target_id)
    if body.is_enabled is not None and body.is_enabled != target.is_enabled:
        await change_vector_index(
            services.vector_index.set_target_enabled(target_id, is_enabled=body.is_enabled)
        )
    updated_target = replace(target, **body.model_dump(exclude_unset=True, exclude_none=True))
    await asyncio.get_running_loop().run_in_executor(
        services.database_thread.executor, partial(save_target, updated_target)
    )
    await publish_configuration_change(
        services.message_bus, owner_id, EntityKind.TARGET, target_id, ChangeKind.UPDATED
    )
    return build_target_response(updated_target)


@router.delete(
    "/watchlists/{watchlist_id}/targets/{target_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_target(
    owner_id: str, watchlist_id: str, target_id: str, services: ServicesDependency
) -> None:
    """Deletes the target with its images and embeddings; its alerts stay."""
    await get_owner_target(services, owner_id, watchlist_id, target_id)
    await change_vector_index(services.vector_index.delete_target(target_id))
    await asyncio.get_running_loop().run_in_executor(
        services.database_thread.executor, partial(delete_target, target_id)
    )
    await asyncio.to_thread(
        services.reference_image_store.delete_target_images, owner_id, target_id
    )
    await publish_configuration_change(
        services.message_bus, owner_id, EntityKind.TARGET, target_id, ChangeKind.DELETED
    )


@router.post(
    "/watchlists/{watchlist_id}/targets/{target_id}/images",
    status_code=status.HTTP_201_CREATED,
)
async def add_images(
    owner_id: str,
    watchlist_id: str,
    target_id: str,
    images: Annotated[list[UploadFile], File()],
    services: ServicesDependency,
) -> TargetResponse:
    """Adds reference images to an existing target and queues them for enrollment."""
    watchlist = await get_owner_watchlist(services, owner_id, watchlist_id)
    target = await get_owner_target(services, owner_id, watchlist_id, target_id)
    uploaded_images = list((await read_uploaded_images(services, images)).values())
    updated_target, uploads_by_image_path = add_uploaded_images(
        services, owner_id, target, uploaded_images
    )
    await write_images(services, uploads_by_image_path)
    try:
        await asyncio.get_running_loop().run_in_executor(
            services.database_thread.executor, partial(save_target, updated_target)
        )
    except Exception:
        await delete_images(services, list(uploads_by_image_path))
        raise
    new_image_ids = {image.id for image in updated_target.reference_images} - {
        image.id for image in target.reference_images
    }
    await announce_new_images(
        services,
        watchlist,
        updated_target,
        [image for image in updated_target.reference_images if image.id in new_image_ids],
        ChangeKind.UPDATED,
    )
    return build_target_response(updated_target)


@router.delete(
    "/watchlists/{watchlist_id}/targets/{target_id}/images/{image_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_image(
    owner_id: str,
    watchlist_id: str,
    target_id: str,
    image_id: str,
    services: ServicesDependency,
) -> None:
    """Deletes a reference image with its embeddings."""
    target = await get_owner_target(services, owner_id, watchlist_id, target_id)
    image = target.find_reference_image(image_id)
    if image is None:
        raise NotFoundError(f"reference image {image_id} does not exist")
    await change_vector_index(services.vector_index.delete_reference_image(image_id))
    await asyncio.get_running_loop().run_in_executor(
        services.database_thread.executor,
        partial(save_target, target.without_reference_image(image_id)),
    )
    await asyncio.to_thread(services.reference_image_store.delete_image, image.image_path)
    await publish_configuration_change(
        services.message_bus, owner_id, EntityKind.TARGET, target_id, ChangeKind.UPDATED
    )


def parse_target_specifications(raw_specifications: str) -> list[TargetSpecificationBody]:
    """Parses the JSON list of target specifications sent next to the files.

    Raises:
        InvalidEntityError: The text is not a valid list of specifications.
    """
    try:
        specifications = TARGET_SPECIFICATIONS_ADAPTER.validate_json(raw_specifications)
    except ValidationError as error:
        raise InvalidEntityError(f"invalid target specifications: {error}") from error
    if not specifications:
        raise InvalidEntityError("a batch needs at least one target")
    return specifications


async def read_uploaded_images(
    services: ApiServices, uploads: list[UploadFile]
) -> dict[str, _UploadedImage]:
    """Reads the uploaded images, keyed by file name.

    Only the type and size are checked here; the detector decides whether an image is usable.

    Raises:
        InvalidEntityError: A file has no or a repeated name, an unsupported type, or is too big.
    """
    maximum_size_bytes = services.settings.api.maximum_image_size_bytes
    uploaded_images: dict[str, _UploadedImage] = {}
    for upload in uploads:
        file_name = upload.filename or ""
        if not file_name or file_name in uploaded_images:
            raise InvalidEntityError("every uploaded file needs a unique name")
        file_suffix = FILE_SUFFIXES_BY_CONTENT_TYPE.get(upload.content_type or "")
        if file_suffix is None:
            raise InvalidEntityError(
                f"{file_name} must be one of {sorted(FILE_SUFFIXES_BY_CONTENT_TYPE)}"
            )
        # Reading one byte past the limit tells a file at the limit from a larger one.
        content = await upload.read(maximum_size_bytes + 1)
        if len(content) > maximum_size_bytes:
            raise InvalidEntityError(f"{file_name} is larger than {maximum_size_bytes} bytes")
        uploaded_images[file_name] = _UploadedImage(
            file_name=file_name, content=content, file_suffix=file_suffix
        )
    return uploaded_images


def add_uploaded_images(
    services: ApiServices,
    owner_id: str,
    target: Target,
    uploaded_images: list[_UploadedImage],
) -> tuple[Target, dict[str, bytes]]:
    """Returns the target with a pending reference image per upload, and each image's content."""
    uploads_by_image_path: dict[str, bytes] = {}
    for uploaded_image in uploaded_images:
        image_id = new_identifier("image")
        image_path = services.reference_image_store.build_image_path(
            owner_id, target.id, image_id, uploaded_image.file_suffix
        )
        target = target.with_reference_image(target.create_reference_image(image_id, image_path))
        uploads_by_image_path[image_path] = uploaded_image.content
    return target, uploads_by_image_path


async def write_images(services: ApiServices, uploads_by_image_path: dict[str, bytes]) -> None:
    """Writes the uploaded images to disk."""
    for image_path, content in uploads_by_image_path.items():
        await asyncio.to_thread(services.reference_image_store.write_image, image_path, content)


async def delete_images(services: ApiServices, image_paths: list[str]) -> None:
    """Deletes images that were written for a change that was not stored."""
    for image_path in image_paths:
        await asyncio.to_thread(services.reference_image_store.delete_image, image_path)


async def announce_new_images(
    services: ApiServices,
    watchlist: Watchlist,
    target: Target,
    images: Sequence[ReferenceImage],
    change_kind: ChangeKind,
) -> None:
    """Queues every embedding of the new images for enrollment and announces the target's change.

    A job that cannot be queued stays pending in the database, and the detector queues it again
    when it next starts.
    """
    try:
        for image in images:
            for embedding in image.embeddings:
                await publish_when_connected(
                    services.message_bus,
                    EnrollmentJobMessage(
                        occurred_at=datetime.now(UTC),
                        owner_id=watchlist.owner_id,
                        target_id=target.id,
                        reference_image_id=image.id,
                        modality=embedding.modality,
                    ),
                )
    except NatsError as error:
        logger.warning("cannot queue enrollment jobs: %s", error, extra={"target_id": target.id})
    await publish_configuration_change(
        services.message_bus, watchlist.owner_id, EntityKind.TARGET, target.id, change_kind
    )


def build_target_response(target: Target) -> TargetResponse:
    """Returns the target's response."""
    return TargetResponse(
        id=target.id,
        watchlist_id=target.watchlist_id,
        label=target.label,
        target_type=target.target_type,
        is_enabled=target.is_enabled,
        metadata=dict(target.metadata),
        enrollment_batch_id=target.enrollment_batch_id,
        enrollment_status=target.enrollment_status,
        reference_images=[
            ReferenceImageResponse(
                id=image.id,
                embeddings=[
                    ImageEmbeddingResponse(
                        modality=embedding.modality,
                        status=embedding.status,
                        rejection_reason=embedding.rejection_reason,
                        quality_score_ratio=(
                            None if embedding.quality is None else score_quality(embedding.quality)
                        ),
                    )
                    for embedding in image.embeddings
                ],
            )
            for image in target.reference_images
        ],
    )
