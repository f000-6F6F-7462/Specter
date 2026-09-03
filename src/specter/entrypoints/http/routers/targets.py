import json
from typing import Annotated

from fastapi import APIRouter, File, Form, Query, UploadFile, status
from pydantic import ValidationError

from specter.application import catalog
from specter.core.errors import RuleViolation
from specter.domain.catalog import EnrollmentStatus
from specter.entrypoints.http.deps import BlobDep, OwnerDep, UowDep
from specter.entrypoints.http.schemas import (
    BatchDeleteOut,
    EnrollmentBatchOut,
    EnrollOut,
    TargetOut,
    TargetSpecIn,
    TargetUpdate,
)

router = APIRouter(tags=["targets"])


def _parse_specs(raw: str) -> tuple[catalog.TargetSpec, ...]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuleViolation(f"'targets' is not valid JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise RuleViolation("'targets' must be a JSON array")
    try:
        specs = [TargetSpecIn.model_validate(item) for item in payload]
    except ValidationError as exc:
        raise RuleViolation(f"invalid target spec: {exc.errors()}") from exc
    return tuple(
        catalog.TargetSpec(
            ref=s.ref,
            label=s.label,
            type=s.type,
            image_names=tuple(s.image_names),
            metadata=dict(s.metadata),
        )
        for s in specs
    )


async def _read_uploads(files: list[UploadFile]) -> tuple[catalog.UploadedImage, ...]:
    out: list[catalog.UploadedImage] = []
    for upload in files:
        out.append(
            catalog.UploadedImage(
                name=upload.filename or "",
                data=await upload.read(),
                content_type=upload.content_type or "application/octet-stream",
            )
        )
    return tuple(out)


@router.post("/watchlists/{watchlist_id}/targets", status_code=status.HTTP_202_ACCEPTED)
async def enroll_targets(
    watchlist_id: str,
    owner: OwnerDep,
    uow: UowDep,
    blob: BlobDep,
    targets: Annotated[str, Form(description="JSON array of target specs")],
    images: Annotated[list[UploadFile] | None, File()] = None,
) -> EnrollOut:
    request = catalog.EnrollTargetsRequest(
        owner_id=owner,
        watchlist_id=watchlist_id,
        targets=_parse_specs(targets),
        images=await _read_uploads(images or []),
    )
    return EnrollOut.of(await catalog.enroll_targets(uow, blob, request))


@router.get("/watchlists/{watchlist_id}/targets")
async def list_targets(
    watchlist_id: str,
    owner: OwnerDep,
    uow: UowDep,
    enrollment_status: Annotated[EnrollmentStatus | None, Query(alias="status")] = None,
) -> list[TargetOut]:
    views = await catalog.list_targets(uow, owner, watchlist_id, status=enrollment_status)
    return [TargetOut.of(v) for v in views]


@router.delete("/watchlists/{watchlist_id}/targets")
async def batch_delete_targets(
    watchlist_id: str,
    owner: OwnerDep,
    uow: UowDep,
    ids: Annotated[str, Query(description="comma-separated target ids")],
) -> BatchDeleteOut:
    target_ids = [tid for tid in (part.strip() for part in ids.split(",")) if tid]
    deleted = await catalog.batch_delete_targets(uow, owner, watchlist_id, target_ids)
    return BatchDeleteOut(deleted=deleted)


@router.get("/targets/{target_id}")
async def get_target(target_id: str, owner: OwnerDep, uow: UowDep) -> TargetOut:
    return TargetOut.of(await catalog.get_target(uow, owner, target_id))


@router.patch("/targets/{target_id}")
async def update_target(
    target_id: str, body: TargetUpdate, owner: OwnerDep, uow: UowDep
) -> TargetOut:
    view = await catalog.update_target(
        uow,
        owner,
        target_id,
        catalog.UpdateTargetRequest(label=body.label, enabled=body.enabled, metadata=body.metadata),
    )
    return TargetOut.of(view)


@router.delete("/targets/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_target(target_id: str, owner: OwnerDep, uow: UowDep) -> None:
    await catalog.delete_target(uow, owner, target_id)


@router.post("/targets/{target_id}/images")
async def add_target_images(
    target_id: str,
    owner: OwnerDep,
    uow: UowDep,
    blob: BlobDep,
    images: Annotated[list[UploadFile], File()],
) -> TargetOut:
    request = catalog.AddImagesRequest(
        owner_id=owner, target_id=target_id, images=await _read_uploads(images)
    )
    return TargetOut.of(await catalog.add_target_images(uow, blob, request))


@router.delete("/targets/{target_id}/images/{image_id}")
async def delete_target_image(
    target_id: str, image_id: str, owner: OwnerDep, uow: UowDep
) -> TargetOut:
    return TargetOut.of(await catalog.delete_target_image(uow, owner, target_id, image_id))


@router.get("/enrollments/{batch_id}")
async def get_enrollment_batch(batch_id: str, owner: OwnerDep, uow: UowDep) -> EnrollmentBatchOut:
    return EnrollmentBatchOut.of(await catalog.get_enrollment_batch(uow, owner, batch_id))
