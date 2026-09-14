"""Enrollment use case: one reference image -> a stored, searchable embedding.

Consumed off ``specter:jobs:enroll`` by ``specter-enroll``. Steps:

1. fetch the image bytes from blob storage
2. detect + quality-gate + align + embed (``FaceEmbeddingService``)
3. on success: upsert the vector, flip the ``ReferenceImage`` row to *embedded*
   on rejection: flip it to *rejected* with the reason
4. publish an ``EnrollmentStatusMessage`` either way
"""

from dataclasses import dataclass

from specter.application.ports import (
    BlobStore,
    EventBus,
    FaceEmbeddingService,
    UnitOfWorkFactory,
    VectorIndex,
)
from specter.contracts import EnrollJobMessage, EnrollmentStatusMessage
from specter.contracts.streams import EVENTS_ENROLLMENT
from specter.core.clock import Clock
from specter.core.ids import new_id
from specter.domain.catalog import Target, Watchlist
from specter.domain.quality import QualityReport, RejectionReason
from specter.domain.vision import Embedding, Vector


@dataclass(frozen=True, slots=True)
class EnrollmentDeps:
    uow_factory: UnitOfWorkFactory
    blob: BlobStore
    faces: FaceEmbeddingService
    vectors: VectorIndex
    bus: EventBus
    clock: Clock


async def run_enrollment(deps: EnrollmentDeps, job: EnrollJobMessage) -> EnrollmentStatusMessage:
    result = await deps.faces.embed_reference(await deps.blob.get(job.blob_key))

    async with deps.uow_factory() as uow:
        target = await uow.targets.get(job.target_id)
        image = target.image(job.image_id) if target is not None else None
        watchlist = await uow.watchlists.get(target.watchlist_id) if target is not None else None
        if target is None or image is None or watchlist is None:
            return _status(deps, job, "rejected", rejection=RejectionReason.NO_DETECTION)

        if result.vector is None:
            reason = result.rejection or RejectionReason.NO_DETECTION
            image.mark_rejected(reason)
            image.quality = result.quality
            await uow.targets.update(target)
            status = _status(deps, job, "rejected", quality=result.quality, rejection=reason)
        else:
            await deps.vectors.upsert(
                [_embedding(job, target, watchlist, result.vector, deps.faces.model_version)]
            )
            image.mark_embedded(deps.faces.model_version)
            image.quality = result.quality
            await uow.targets.update(target)
            status = _status(deps, job, "embedded", quality=result.quality)

    await deps.bus.publish(EVENTS_ENROLLMENT, job.target_id, status)
    return status


def _embedding(
    job: EnrollJobMessage,
    target: Target,
    watchlist: Watchlist,
    vector: Vector,
    model_version: str,
) -> Embedding:
    return Embedding(
        modality=job.modality,
        vector=vector,
        target_id=target.id,
        image_id=job.image_id,
        payload={
            "owner_id": watchlist.owner_id,
            "watchlist_id": watchlist.id,
            "target_id": target.id,
            "image_id": job.image_id,
            "model_version": model_version,
            "enabled": target.enabled,
        },
    )


def _status(
    deps: EnrollmentDeps,
    job: EnrollJobMessage,
    outcome: str,
    *,
    quality: QualityReport | None = None,
    rejection: RejectionReason | None = None,
) -> EnrollmentStatusMessage:
    return EnrollmentStatusMessage(
        event_id=new_id("evt"),
        occurred_at=deps.clock.wall(),
        owner_id=job.owner_id,
        batch_id=job.batch_id,
        target_id=job.target_id,
        image_id=job.image_id,
        status=outcome,
        quality_score=quality.score if quality is not None else None,
        rejection_reason=rejection.value if rejection is not None else None,
    )
